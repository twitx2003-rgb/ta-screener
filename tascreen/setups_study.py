"""The owner's setups through history (owner, 2026-10-06: "an expert agent that researches at
length, goes over the setups daily, sees why setups fell and left the watchlist, and knows
what to improve and what to keep"). This is the study it starts from and measures against.

Every stored stock is walked as the setups list would have seen it, with no look-ahead (each
setup uses only the bars up to its own session):
- ma150 / ma20: a test of the average that held (analyst/support.py `walk`), with the
  conditions the list uses or might use: the bounce's volume, the way down's volume, the
  average's slope, holds in a row, the distance from the 52-week high;
- breakout: a bullish chart-pattern breakout of the outcome ledger (its own no-look-ahead
  record), with its volume;
- retest: such a breakout's broken line, followed with its slope, tested and held.
Each is measured from its session's close over `horizon` sessions: failed (a close more than
`support_break_atr` ATRs under its level, the list's own exit), the return after 5/10/20
sessions, and the best and worst move. The market's breadth that day (the share of stocks
above their 150-day average) is added, and a baseline: sessions of stocks above a rising
150-day average, measured the same way, so a setup is judged against simply being in a rise.
Our measurements, not Bulkowski's statistics.
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .analyst import support
from .indicators import atr as atr_series
from .indicators import sma
from .patterns.levels import day_of

HORIZON = 10
RETURNS = (5, 10, 20)
WEDGES = ("rising_wedge", "falling_wedge")
BASELINE_EVERY = 5            # every 5th session of a rise is a baseline sample


def _measure(close, high, low, level, atr, d: int, break_atr: float, horizon: int = HORIZON) -> dict[str, Any] | None:
    """The next sessions after a setup at session d (None without `horizon` of them)."""
    n = len(close)
    if d + horizon >= n:
        return None
    entry = close[d]
    failed_at = None
    for k in range(1, horizon + 1):
        lv, a = level[d + k], atr[d + k]
        if math.isfinite(lv) and math.isfinite(a) and close[d + k] < lv - break_atr * a:
            failed_at = k
            break
    out = {"failed": failed_at is not None, "failed_after": failed_at,
           "mfe_pct": round((float(np.max(high[d + 1:d + horizon + 1])) / entry - 1) * 100, 2),
           "mae_pct": round((float(np.min(low[d + 1:d + horizon + 1])) / entry - 1) * 100, 2)}
    for r in RETURNS:
        out[f"ret{r}_pct"] = round((close[d + r] / entry - 1) * 100, 2) if d + r < n else None
    return out


def study_stock(symbol: str, bars: pd.DataFrame, breakouts: list[dict], rules: dict[str, Any],
                horizon: int = HORIZON) -> dict[str, Any]:
    """Every setup of one stock, measured; its baseline samples; and the days it was above
    its 150-day average (for the market's breadth)."""
    from .analyst.facts import _broken_line

    bars = bars.reset_index(drop=True)
    close, high, low = (bars[c].to_numpy(float) for c in ("close", "high", "low"))
    days = bars["timestamp"].map(day_of).astype(str).tolist()
    atr = atr_series(bars, 14).to_numpy(float)
    ratios = support.volume_ratios(bars["volume"])
    vol10 = bars["volume"].rolling(10).mean().to_numpy(float)
    ma = {n: sma(bars["close"], n).to_numpy(float) for n in (20, 50, 150)}
    slope = {n: (pd.Series(ma[n]) / pd.Series(ma[n]).shift(10) - 1).to_numpy(float) * 100 for n in (20, 150)}
    high252 = bars["high"].rolling(252, min_periods=120).max().to_numpy(float)
    brk = float(rules["support_break_atr"])
    setups: list[dict[str, Any]] = []

    def context(d: int) -> dict[str, Any]:
        return {"symbol": symbol, "day": days[d], "liquid": bool(vol10[d] >= 1e6) if math.isfinite(vol10[d]) else False,
                "above150": bool(close[d] > ma[150][d]) if math.isfinite(ma[150][d]) else None,
                "slope150_pct": round(float(slope[150][d]), 2) if math.isfinite(slope[150][d]) else None,
                "from_high_pct": round((close[d] / high252[d] - 1) * 100, 1) if math.isfinite(high252[d]) else None,
                "above50": bool(close[d] > ma[50][d]) if math.isfinite(ma[50][d]) else None}

    for n in (150, 20):                                    # the averages' holds
        streak = 0
        for t in support.walk(bars, ma[n], atr, n, rules, ratios):
            if t.result == support.BROKE:
                streak = 0
                continue
            if t.result != support.HELD:
                continue
            streak += 1
            got = _measure(close, high, low, ma[n], atr, t.end, brk, horizon)
            if got is None:
                continue
            setups.append({"kind": f"ma{n}", **context(t.end), "volume": _r(t.volume_ratio),
                           "pullback_volume": _r(t.pullback_ratio), "streak": streak,
                           "slope_pct": _r(slope[n][t.end]), "test_sessions": t.end - t.touch, **got})

    index = {day: i for i, day in enumerate(days)}
    for b in breakouts:                                    # the ledger's bullish breakouts
        if b["pattern"] in WEDGES or b["breakout_day"] not in index:
            continue
        at = index[b["breakout_day"]]
        line_at = _broken_line(b["lines"], float(b["breakout_price"]), index, at)
        levels = np.full(len(close), np.nan)
        levels[at:] = [line_at(i) for i in range(at, len(close))]
        got = _measure(close, high, low, levels, atr, at, brk, horizon)
        if got is not None:
            setups.append({"kind": "breakout", "pattern": b["pattern"], **context(at),
                           "volume": _r(ratios[at]), **got})
        for t in support.walk(bars, levels, atr, at, rules, ratios):
            if t.result != support.HELD:
                continue
            got = _measure(close, high, low, levels, atr, t.end, brk, horizon)
            if got is not None:
                setups.append({"kind": "retest", "pattern": b["pattern"], **context(t.end),
                               "volume": _r(t.volume_ratio), "pullback_volume": _r(t.pullback_ratio),
                               "after_breakout": t.end - at, **got})

    baseline = []                                          # sessions of a rise, for comparison
    for d in range(160, len(close) - horizon, BASELINE_EVERY):
        if math.isfinite(ma[150][d]) and close[d] > ma[150][d] and math.isfinite(slope[150][d]) and slope[150][d] > 0.3:
            got = _measure(close, high, low, ma[150], atr, d, brk, horizon)
            if got is not None:
                baseline.append({"kind": "baseline", **context(d), **got})
    above = {days[d]: bool(close[d] > ma[150][d]) for d in range(150, len(close)) if math.isfinite(ma[150][d])}
    return {"setups": setups, "baseline": baseline, "above": above}


def _r(x: Any, digits: int = 2) -> float | None:
    try:
        return round(float(x), digits) if math.isfinite(float(x)) else None
    except (TypeError, ValueError):
        return None


def _ledger_breakouts(ledger: pd.DataFrame | None) -> dict[str, list[dict]]:
    """symbol -> its bullish breakouts (first record of each), lines parsed."""
    out: dict[str, list[dict]] = {}
    if ledger is None or ledger.empty:
        return out
    rows = ledger.loc[ledger["direction"] == "bullish"].drop_duplicates("key")
    for r in rows.to_dict("records"):
        try:
            lines = json.loads(r.get("lines_json") or "[]")
        except ValueError:
            lines = []
        out.setdefault(r["symbol"], []).append({"pattern": r["pattern"], "breakout_day": str(r["breakout_day"])[:10],
                                                "breakout_price": r["breakout_price"], "lines": lines})
    return out


def _one(job):
    from .analyst import load_rules
    from .store import Store

    root, symbol, breakouts = job
    try:
        bars = Store(Path(root)).read_bars(symbol)
        if bars is None or len(bars) < 200:
            return symbol, None
        return symbol, study_stock(symbol, bars, breakouts, load_rules())
    except Exception as exc:                               # one stock never stops the study
        return symbol, {"error": type(exc).__name__}


def run_study(root: Path, symbols: list[str], ledger: pd.DataFrame | None, workers: int = 4) -> pd.DataFrame:
    """Every setup and baseline sample of `symbols`, one row each, with the day's breadth."""
    by = _ledger_breakouts(ledger)
    jobs = [(str(root), s, by.get(s, [])) for s in symbols]
    rows, above = [], {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for symbol, got in pool.map(_one, jobs, chunksize=8):
            if not got or "error" in got:
                continue
            rows += got["setups"] + got["baseline"]
            for day, up in got["above"].items():
                n, k = above.get(day, (0, 0))
                above[day] = (n + 1, k + int(up))
    frame = pd.DataFrame(rows)
    breadth = {day: round(k / n * 100, 1) for day, (n, k) in above.items() if n >= 50}
    if not frame.empty:
        frame["breadth_pct"] = frame["day"].map(breadth)
    return frame


# ------------------------------------------------------------------ the summary
def _stats(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {"n": 0}
    return {"n": int(len(rows)), "failed_pct": round(rows["failed"].mean() * 100, 1),
            "win10_pct": round((rows["ret10_pct"] > 0).mean() * 100, 1),
            "ret10_avg": round(rows["ret10_pct"].mean(), 2), "ret10_median": round(rows["ret10_pct"].median(), 2),
            "mfe10_avg": round(rows["mfe_pct"].mean(), 2), "mae10_avg": round(rows["mae_pct"].mean(), 2)}


BUCKETS = {
    "volume": ([0, 0.85, 1.15, 1.5, math.inf], ["weak <0.85", "normal 0.85-1.15", "above 1.15-1.5", "high >=1.5"]),
    "from_high_pct": ([-math.inf, -25, -10, -3, math.inf], ["far >25% under", "10-25% under", "3-10% under", "near the high"]),
    "breadth_pct": ([0, 40, 60, 101], ["weak market <40%", "mixed 40-60%", "strong market >60%"]),
    "slope_pct": ([-math.inf, 0, 1, math.inf], ["falling", "rising <1%", "rising >=1%"]),
}


def summarise(frame: pd.DataFrame) -> dict[str, Any]:
    """The study's tables: each kind (liquid stocks, the list's trend rule) against the
    baseline, and each condition's buckets within each kind."""
    out: dict[str, Any] = {"baseline": _stats(frame[(frame["kind"] == "baseline") & frame["liquid"]])}
    for kind in ("ma150", "ma20", "retest", "breakout"):
        rows = frame[(frame["kind"] == kind) & frame["liquid"]]
        trend = rows[(rows["above150"] == True) & (rows["slope150_pct"] > 0.3)]       # noqa: E712
        part: dict[str, Any] = {"all": _stats(rows), "uptrend": _stats(trend)}
        for col, (edges, labels) in BUCKETS.items():
            if col in trend and trend[col].notna().any():
                cut = pd.cut(trend[col], edges, labels=labels, right=False)
                part[col] = {str(label): _stats(trend[cut == label]) for label in labels}
        if "streak" in trend:
            part["streak"] = {"first hold": _stats(trend[trend["streak"] == 1]),
                              "second hold": _stats(trend[trend["streak"] == 2]),
                              "third or more": _stats(trend[trend["streak"] >= 3])}
        if kind in ("retest", "breakout"):
            part["pattern"] = {p: _stats(g) for p, g in trend.groupby("pattern") if len(g) >= 30}
        out[kind] = part
    return out
