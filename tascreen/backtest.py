"""A backtest of the breakout ledger, like a strategy tester (the owner's request after
a trading video, 2026-09-25): every decided breakout in the outcome ledger (the daily
scans and the no-look-ahead backfill, tascreen/outcomes.py) taken as one trade.

- Entry: the open of the session after the breakout day (a breakout is known only at
  its close). A trade whose entry is already past its target is skipped (nothing left
  to take), and so is one the ledger resolved before the entry.
- Exit: at the measure-rule target when the ledger says it was reached (a limit order,
  filled at the target); otherwise the close of the session the ledger resolved it on:
  the close beyond the invalidation level (failed) or the last tracked session (expired).
- Return: % of the entry, long for bullish patterns, short for bearish ones. No
  commissions, no slippage, one trade at a time per breakout.

Prices: the ledger's levels are as seen on the breakout day; the bars may have been
adjusted for a split since, so the target is scaled by the ledger's own `scale` (the
bars' close on the breakout day over the ledger's breakout close).

Per pattern and direction, and for all together: trades, win rate, average and median
return, profit factor (gains over losses), average sessions held, best and worst trade,
and how far a trade went against it before its exit (max adverse excursion from the
entry: the median and the worst 10%), plus the longest losing streak in exit order.
With thousands of overlapping trades, one equity curve's drawdown would mean nothing.

The universe is today's $1B+ list: stocks that did well enough to be in it now. The
bullish results lean optimistic for that reason (survivorship).
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Callable

import numpy as np
import pandas as pd

from .outcomes import FINAL
from .patterns.levels import day_of

BULL, BEAR = "bullish", "bearish"


def trades(ledger: pd.DataFrame | None, bars_of: Callable[[str], pd.DataFrame | None]) -> pd.DataFrame:
    columns = ["symbol", "pattern", "direction", "source", "outcome", "entry_day", "exit_day",
               "entry", "exit", "return_pct", "sessions", "mae_pct"]
    if ledger is None or ledger.empty:
        return pd.DataFrame(columns=columns)
    decided = ledger.loc[ledger["outcome"].isin(FINAL) & ledger["direction"].isin([BULL, BEAR])]
    rows = []
    for symbol, group in decided.groupby("symbol", sort=False):
        bars = bars_of(symbol)
        if bars is None or bars.empty:
            continue
        days = np.array([ts.date() for ts in bars["timestamp"]])
        opens, closes = bars["open"].to_numpy(float), bars["close"].to_numpy(float)
        highs, lows = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
        for r in group.itertuples(index=False):
            broke, resolved = day_of(r.breakout_day), day_of(r.resolved_day)
            if broke is None or resolved is None:
                continue
            i = int(np.searchsorted(days, broke, side="right"))          # the next session
            if i >= len(days) or resolved < days[i]:
                continue
            j = int(np.searchsorted(days, resolved, side="left"))
            if j >= len(days) or days[j] != resolved:
                continue
            entry, sign = opens[i], 1.0 if r.direction == BULL else -1.0
            scale = float(r.scale) if r.scale is not None and math.isfinite(float(r.scale)) else 1.0
            target = float(r.target) * scale if r.target is not None else math.nan
            if not (math.isfinite(entry) and entry > 0):
                continue
            if math.isfinite(target) and (target - entry) * sign <= 0:
                continue                                                   # already past it
            exit_price = target if r.outcome == "target" and math.isfinite(target) else closes[j]
            worst = lows[i:j + 1].min() if sign > 0 else highs[i:j + 1].max()
            mae = max(0.0, (1 - worst / entry) * 100 if sign > 0 else (worst / entry - 1) * 100)
            rows.append((symbol, r.pattern, r.direction, r.source, r.outcome, days[i], resolved,
                         entry, exit_price, (exit_price / entry - 1) * 100 * sign, j - i + 1, mae))
    return pd.DataFrame(rows, columns=columns)


def _losing_streak(frame: pd.DataFrame) -> int:
    ordered = frame.sort_values(["exit_day", "entry_day"], kind="stable")["return_pct"].to_numpy(float)
    longest = current = 0
    for value in ordered:
        current = current + 1 if value <= 0 else 0
        longest = max(longest, current)
    return longest


def _stats(frame: pd.DataFrame) -> dict[str, Any]:
    r = frame["return_pct"].to_numpy(float)
    mae = frame["mae_pct"].to_numpy(float)
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {"trades": int(len(r)),
            "win_pct": round(float((r > 0).mean() * 100), 1),
            "avg_return_pct": round(float(r.mean()), 2),
            "median_return_pct": round(float(np.median(r)), 2),
            "profit_factor": round(float(gains / losses), 2) if losses > 0 else None,
            "avg_sessions": round(float(frame["sessions"].mean()), 1),
            "best_pct": round(float(r.max()), 1), "worst_pct": round(float(r.min()), 1),
            "median_mae_pct": round(float(np.median(mae)), 1),
            "p90_mae_pct": round(float(np.percentile(mae, 90)), 1),
            "losing_streak": _losing_streak(frame),
            "target_pct": round(float((frame["outcome"] == "target").mean() * 100), 1)}


def summary(done: pd.DataFrame, min_trades: int) -> list[dict[str, Any]]:
    """One row per (pattern, direction) with at least `min_trades`, then the totals per
    direction ("all"). Sorted: the totals first, then by average return."""
    if done.empty:
        return []
    out = []
    for direction in (BULL, BEAR):
        mine = done.loc[done["direction"] == direction]
        if len(mine):
            out.append({"pattern": "all", "direction": direction, **_stats(mine)})
    rows = [{"pattern": pattern, "direction": direction, **_stats(group)}
            for (pattern, direction), group in done.groupby(["pattern", "direction"])
            if len(group) >= min_trades]
    rows.sort(key=lambda row: (row["direction"] != BULL, -row["avg_return_pct"]))
    return out + rows


def period(done: pd.DataFrame) -> dict[str, str | None]:
    if done.empty:
        return {"from": None, "to": None}
    first, last = min(done["entry_day"]), max(done["exit_day"])
    return {"from": first.isoformat() if isinstance(first, date) else str(first),
            "to": last.isoformat() if isinstance(last, date) else str(last)}
