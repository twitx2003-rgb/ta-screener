"""Minor highs and lows (Bulkowski's turning points) by an ATR-scaled zigzag.

A high becomes a pivot once price has fallen `k` ATRs below it; a low once price
has risen `k` ATRs above it. Scaling by ATR makes one setting work for a quiet
utility and a volatile small cap alike. Pivots alternate H, L, H, L ...

The last swing is not a pivot until price reverses enough — a pattern can only
use confirmed pivots, which is what makes a detection stable from day to day.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Pivot:
    i: int                # bar index
    price: float          # the high (for H) or the low (for L)
    kind: str             # "H" | "L"


def zigzag(high: np.ndarray, low: np.ndarray, atr: np.ndarray, k: float) -> list[Pivot]:
    n = len(high)
    pivots: list[Pivot] = []
    start = next((i for i in range(n) if not math.isnan(atr[i])), None)
    if start is None:
        return pivots
    hi_p, hi_i = high[start], start
    lo_p, lo_i = low[start], start
    trend = 0                                  # 0 unknown, +1 rising, -1 falling
    for i in range(start + 1, n):
        threshold = k * atr[i]
        if trend >= 0 and high[i] > hi_p:
            hi_p, hi_i = high[i], i
        if trend <= 0 and low[i] < lo_p:
            lo_p, lo_i = low[i], i
        if trend == 0:
            if hi_p - low[i] >= threshold and hi_i < i:
                pivots.append(Pivot(hi_i, float(hi_p), "H"))
                trend, lo_p, lo_i = -1, low[i], i
            elif high[i] - lo_p >= threshold and lo_i < i:
                pivots.append(Pivot(lo_i, float(lo_p), "L"))
                trend, hi_p, hi_i = 1, high[i], i
        elif trend == 1:
            if hi_p - low[i] >= threshold and hi_i < i:
                pivots.append(Pivot(hi_i, float(hi_p), "H"))
                trend, lo_p, lo_i = -1, low[i], i
        else:
            if high[i] - lo_p >= threshold and lo_i < i:
                pivots.append(Pivot(lo_i, float(lo_p), "L"))
                trend, hi_p, hi_i = 1, high[i], i
    return pivots
