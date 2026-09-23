"""Technical indicators computed from our own daily bars.

TradingView's screener has no moving-average or candle columns (live
catalogue, 2026-09-23), so everything is computed here. Its RSI, EMA50 and
EMA200 do come back with the universe; `cross_check` compares ours against
them as a sanity check on the bars and the formulas.

Conventions:
- RSI and ATR use Wilder's smoothing (an EMA with alpha = 1/n), as TradingView
  does. pandas seeds that EMA with the first value rather than an n-bar average;
  with hundreds of bars the difference has decayed away.
- EMA uses alpha = 2/(n+1).
- A value that needs more history than the symbol has is NaN, never a guess.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    out = series.ewm(span=n, adjust=False).mean()
    out.iloc[:n - 1] = np.nan              # not enough history for a meaningful value
    return out


def wilder(series: pd.Series, n: int) -> pd.Series:
    out = series.ewm(alpha=1 / n, adjust=False).mean()
    out.iloc[:n] = np.nan
    return out


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    change = close.diff()
    gain = wilder(change.clip(lower=0).fillna(0), n)
    loss = wilder((-change).clip(lower=0).fillna(0), n)
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def true_range(bars: pd.DataFrame) -> pd.Series:
    prev = bars["close"].shift(1)
    return pd.concat([bars["high"] - bars["low"], (bars["high"] - prev).abs(),
                      (bars["low"] - prev).abs()], axis=1).max(axis=1)


def atr(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(bars), n)


def _days_since_cross(fast: pd.Series, slow: pd.Series, upward: bool, within: int) -> float:
    above = fast > slow
    valid = fast.notna() & slow.notna()
    crossed = (above & ~above.shift(1, fill_value=False)) if upward else \
              (~above & above.shift(1, fill_value=True))
    crossed &= valid & valid.shift(1, fill_value=False)
    hits = np.flatnonzero(crossed.to_numpy()[-within:])
    return float(within - 1 - hits[-1]) if hits.size else math.nan


def _pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if b and not math.isnan(a) and not math.isnan(b) else math.nan


def latest(bars: pd.DataFrame, cross_within: int = 20) -> dict[str, Any]:
    """The indicator values on the last bar."""
    close = bars["close"]
    last = float(close.iloc[-1])
    s20, s50, s200 = sma(close, 20), sma(close, 50), sma(close, 200)
    e21, e50, e200 = ema(close, 21), ema(close, 50), ema(close, 200)
    a14 = atr(bars, 14)
    window = bars.iloc[-252:]
    high52, low52 = float(window["high"].max()), float(window["low"].min())
    vol = bars["volume"]
    avg_vol50 = float(vol.iloc[-51:-1].mean()) if len(vol) > 50 else math.nan
    dollar20 = float((close * vol).iloc[-20:].mean()) if len(vol) >= 20 else math.nan

    def at(series: pd.Series, back: int = 0) -> float:
        return float(series.iloc[-1 - back]) if len(series) > back else math.nan

    return {
        "last_date": bars["timestamp"].iloc[-1].date().isoformat(),
        "bars": len(bars),
        "close": last,
        "change_1d_pct": _pct(last, at(close, 1)),
        "change_5d_pct": _pct(last, at(close, 5)),
        "change_20d_pct": _pct(last, at(close, 20)),
        "sma20": at(s20), "sma50": at(s50), "sma200": at(s200),
        "ema21": at(e21), "ema50": at(e50), "ema200": at(e200),
        "above_sma50": bool(last > at(s50)) if not math.isnan(at(s50)) else None,
        "above_sma200": bool(last > at(s200)) if not math.isnan(at(s200)) else None,
        "sma50_slope_10d_pct": _pct(at(s50), at(s50, 10)),
        "rsi14": at(rsi(close, 14)),
        "atr14": at(a14),
        "atr_pct": at(a14) / last * 100 if last else math.nan,
        "high_52w": high52, "low_52w": low52,
        "pct_from_52w_high": _pct(last, high52),
        "pct_from_52w_low": _pct(last, low52),
        "rel_volume": float(vol.iloc[-1]) / avg_vol50 if avg_vol50 else math.nan,
        "avg_dollar_volume_20d": dollar20,
        "golden_cross_days_ago": _days_since_cross(s50, s200, True, cross_within),
        "death_cross_days_ago": _days_since_cross(s50, s200, False, cross_within),
    }


# TradingView's own values, returned with the universe, against ours.
CROSS_CHECKS = (("rsi14", "tv_rsi", "abs", 1.0),         # RSI points
                ("ema50", "tv_ema50", "pct", 0.5),       # percent of price
                ("ema200", "tv_ema200", "pct", 0.5))


def cross_check(frame: pd.DataFrame) -> dict[str, Any]:
    """Share of symbols where our value agrees with TradingView's, per indicator.

    Only meaningful when the universe snapshot was taken outside market hours:
    during the session TradingView computes on the live price, we on the last close.
    """
    report = {}
    for ours, theirs, kind, tolerance in CROSS_CHECKS:
        both = frame[[ours, theirs]].dropna()
        if both.empty:
            report[ours] = {"compared": 0}
            continue
        diff = (both[ours] - both[theirs]).abs()
        if kind == "pct":
            diff = diff / both[theirs].abs() * 100
        agree = diff <= tolerance
        worst = diff.sort_values(ascending=False).head(5)
        report[ours] = {
            "compared": int(len(both)), "agree": int(agree.sum()),
            "agree_share": round(float(agree.mean()), 4),
            "tolerance": f"{tolerance} {'points' if kind == 'abs' else '%'}",
            "median_diff": round(float(diff.median()), 4),
            "worst": {frame.loc[i, "symbol"]: round(float(v), 3) for i, v in worst.items()},
        }
    return report
