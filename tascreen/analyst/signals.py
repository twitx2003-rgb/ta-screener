"""Indicators for the analyst: MACD, RSI divergences, the moving-average stack, and the
volume profile (volume by price).

- MACD: EMA(fast) - EMA(slow), its EMA(signal), and the difference (histogram).
- Divergence: the last two turning points of one kind, the second within
  `divergence_recent_sessions`: price makes a lower low while RSI (or MACD) makes a
  higher low -> bullish; a higher high with a lower indicator high -> bearish. A turning
  point is known only once price has reversed, so a divergence is reported late.
- Moving-average stack: 50 > 150 > 200 (and price above) is a bullish stack, the reverse
  bearish, anything else mixed.
- Volume profile: each bar's volume spread evenly over its high-low range, in
  `volume_profile_bins` price bins over the window; the point of control (POC) is the
  fullest bin, the value area the bins around it holding `value_area` of the volume.
  Built from daily bars, so it approximates (and differs from) TradingView's profile.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..indicators import ema, rsi, sma
from ..patterns.pivots import Pivot


def macd(close: pd.Series, fast: int, slow: int, signal: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


@dataclass
class Divergence:
    id: str
    kind: str          # bullish | bearish
    indicator: str     # rsi | macd
    i1: int
    i2: int
    price1: float
    price2: float
    value1: float
    value2: float
    day1: str = ""
    day2: str = ""


def divergences(bars: pd.DataFrame, piv: list[Pivot], rsi_values: pd.Series, macd_line: pd.Series,
                recent: int) -> list[Divergence]:
    last = len(bars) - 1
    out = []
    for pivot_kind, kind in (("L", "bullish"), ("H", "bearish")):
        same = [p for p in piv if p.kind == pivot_kind]
        if len(same) < 2:
            continue
        p1, p2 = same[-2], same[-1]
        if last - p2.i > recent:
            continue
        price_new_extreme = p2.price < p1.price if kind == "bullish" else p2.price > p1.price
        if not price_new_extreme:
            continue
        for name, series in (("rsi", rsi_values), ("macd", macd_line)):
            v1, v2 = float(series.iloc[p1.i]), float(series.iloc[p2.i])
            if not (math.isfinite(v1) and math.isfinite(v2)):
                continue
            if (kind == "bullish" and v2 > v1) or (kind == "bearish" and v2 < v1):
                out.append(Divergence("", kind, name, p1.i, p2.i, p1.price, p2.price,
                                      round(v1, 4), round(v2, 4),
                                      bars["timestamp"].iloc[p1.i].date().isoformat(),
                                      bars["timestamp"].iloc[p2.i].date().isoformat()))
    for n, d in enumerate(out, 1):
        d.id = f"div_{n}"
    return out


def ma_stack(close: pd.Series, periods: list[int]) -> dict[str, Any]:
    values = {n: float(sma(close, n).iloc[-1]) for n in periods}
    price = float(close.iloc[-1])
    known = [values[n] for n in periods if math.isfinite(values[n])]
    if len(known) < len(periods):
        state = "unknown"
    elif price > known[0] and all(x > y for x, y in zip(known, known[1:])):
        state = "bullish"
    elif price < known[0] and all(x < y for x, y in zip(known, known[1:])):
        state = "bearish"
    else:
        state = "mixed"
    return {"values": values, "state": state}


def volume_profile(window: pd.DataFrame, bins: int, area: float) -> dict[str, Any] | None:
    lo, hi = float(window["low"].min()), float(window["high"].max())
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    volume = np.zeros(bins)
    for low, high, vol in window[["low", "high", "volume"]].itertuples(index=False):
        if not (math.isfinite(vol) and vol > 0):
            continue
        span = max(high - low, 1e-9)
        overlap = np.clip(np.minimum(edges[1:], high) - np.maximum(edges[:-1], low), 0, None)
        if overlap.sum() <= 0:            # a bar with no range: its whole volume in one bin
            overlap[min(np.searchsorted(edges, low, side="right") - 1, bins - 1)] = span
        volume += vol * overlap / overlap.sum()
    poc = int(np.argmax(volume))
    total, inside = volume.sum(), volume[poc]
    a, b = poc, poc
    while inside < area * total and (a > 0 or b < bins - 1):
        left = volume[a - 1] if a > 0 else -1
        right = volume[b + 1] if b < bins - 1 else -1
        if right >= left:
            b += 1
            inside += volume[b]
        else:
            a -= 1
            inside += volume[a]
    mids = (edges[:-1] + edges[1:]) / 2
    return {"edges": [round(float(x), 4) for x in edges], "volume": [float(x) for x in volume],
            "poc": round(float(mids[poc]), 4), "val": round(float(edges[a]), 4),
            "vah": round(float(edges[b + 1]), 4)}


def volume_trend(volume: pd.Series, short: int, long: int) -> dict[str, Any]:
    s, l = float(volume.tail(short).mean()), float(volume.tail(long).mean())
    ratio = s / l if l else math.nan
    word = "rising" if ratio > 1.15 else "falling" if ratio < 0.85 else "flat"
    return {"ratio": round(ratio, 2) if math.isfinite(ratio) else math.nan, "trend": word}


def rsi_series(close: pd.Series, period: int) -> pd.Series:
    return rsi(close, period)
