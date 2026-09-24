"""Levels drawn from turning points: support/resistance zones, trendlines, Fibonacci.

- Zones: turning points (pivots.zigzag at `minor_pivot_atr`) whose prices lie within
  `zone_merge_atr` ATRs of each other are one zone; a zone tested at least
  `zone_min_touches` times is kept, the nearest `zones_each_side` above and below the
  last close. Above the close it is resistance, below it support.
- Trendlines: through two turning points of the same kind (lows for support, highs for
  resistance), confirmed by a third within `trendline_touch_atr` ATRs, never closed
  through by more than `trendline_break_atr` ATRs since the first point, last touched
  within `trendline_recent_sessions`. The best one per kind: most touches, then longest.
- Fibonacci: the last completed major swing (pivots at `major_pivot_atr`): retracement
  levels back from its end, extensions beyond it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..patterns.pivots import Pivot, zigzag


@dataclass
class Zone:
    id: str
    kind: str                 # support | resistance
    low: float
    high: float
    touches: int
    days: list[str] = field(default_factory=list)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


@dataclass
class Trendline:
    id: str
    kind: str                 # support | resistance
    i1: int
    i2: int                   # the two turning points it is drawn through
    a: float                  # price = a + b * bar index
    b: float
    touches: int
    last_touch: int
    day1: str = ""
    day2: str = ""

    def at(self, i: float) -> float:
        return self.a + self.b * i


@dataclass
class Fibonacci:
    direction: str            # up | down (the swing)
    start_i: int
    end_i: int
    start: float
    end: float
    levels: dict[str, float]  # "fib_382" -> price, "fib_ext_1618" -> price
    position: float           # how far the last close has retraced the swing (0..1+)
    start_day: str = ""
    end_day: str = ""


def pivots(bars: pd.DataFrame, atr: np.ndarray, k: float) -> list[Pivot]:
    return zigzag(bars["high"].to_numpy(float), bars["low"].to_numpy(float), atr, k)


def _day(bars: pd.DataFrame, i: int) -> str:
    return bars["timestamp"].iloc[i].date().isoformat()


def sr_zones(bars: pd.DataFrame, piv: list[Pivot], atr_now: float, rules: dict[str, Any]) -> list[Zone]:
    if not piv or not math.isfinite(atr_now) or atr_now <= 0:
        return []
    close = float(bars["close"].iloc[-1])
    merge = rules["zone_merge_atr"] * atr_now
    widest = rules["zone_max_width_atr"] * atr_now
    groups: list[list[Pivot]] = []
    for p in sorted(piv, key=lambda p: p.price):
        if groups and p.price - groups[-1][-1].price <= merge and p.price - groups[-1][0].price <= widest:
            groups[-1].append(p)
        else:
            groups.append([p])
    zones = []
    for group in groups:
        if len(group) < rules["zone_min_touches"]:
            continue
        low, high = min(p.price for p in group), max(p.price for p in group)
        pad = max(0.0, rules["zone_min_width_atr"] * atr_now - (high - low)) / 2
        low, high = low - pad, high + pad
        kind = "resistance" if (low + high) / 2 > close else "support"
        zones.append(Zone("", kind, round(low, 4), round(high, 4), len(group),
                          sorted({_day(bars, p.i) for p in group})))
    above = sorted((z for z in zones if z.kind == "resistance"), key=lambda z: z.mid)
    below = sorted((z for z in zones if z.kind == "support"), key=lambda z: -z.mid)
    keep = above[:rules["zones_each_side"]] + below[:rules["zones_each_side"]]
    keep.sort(key=lambda z: -z.mid)
    for n, zone in enumerate(keep, 1):
        zone.id = f"zone_{n}"
    return keep


def _best_line(bars: pd.DataFrame, points: list[Pivot], atr: np.ndarray, kind: str,
               rules: dict[str, Any]) -> Trendline | None:
    closes = bars["close"].to_numpy(float)
    last = len(bars) - 1
    best = None
    for x in range(len(points)):
        for y in range(x + 1, len(points)):
            p, q = points[x], points[y]
            if q.i == p.i:
                continue
            b = (q.price - p.price) / (q.i - p.i)
            a = p.price - b * p.i
            idx = np.arange(p.i, last + 1)
            line = a + b * idx
            tol = rules["trendline_break_atr"] * np.nan_to_num(atr[p.i:last + 1], nan=np.nanmedian(atr))
            beyond = closes[p.i:] < line - tol if kind == "support" else closes[p.i:] > line + tol
            if beyond.any():
                continue                        # closed through: not a live line
            touch = rules["trendline_touch_atr"]
            touching = [r for r in points if r.i >= p.i
                        and abs(r.price - (a + b * r.i)) <= touch * (atr[r.i] if math.isfinite(atr[r.i]) else 0)]
            if len(touching) < rules["trendline_min_touches"]:
                continue
            last_touch = max(r.i for r in touching)
            if last - last_touch > rules["trendline_recent_sessions"]:
                continue
            now_atr = atr[last] if math.isfinite(atr[last]) else np.nanmedian(atr)
            if abs(a + b * last - closes[last]) > rules["trendline_max_distance_atr"] * now_atr:
                continue
            key = (len(touching), last_touch - p.i)
            if best is None or key > best[0]:
                best = (key, Trendline("", kind, p.i, q.i, a, b, len(touching), last_touch,
                                        _day(bars, p.i), _day(bars, q.i)))
    return best[1] if best else None


def trendlines(bars: pd.DataFrame, piv: list[Pivot], atr: np.ndarray,
               rules: dict[str, Any]) -> list[Trendline]:
    out = []
    for kind, pivot_kind in (("support", "L"), ("resistance", "H")):
        line = _best_line(bars, [p for p in piv if p.kind == pivot_kind], atr, kind, rules)
        if line is not None:
            out.append(line)
    for n, line in enumerate(out, 1):
        line.id = f"tl_{n}"
    return out


def fibonacci(bars: pd.DataFrame, major: list[Pivot], rules: dict[str, Any]) -> Fibonacci | None:
    if len(major) < 2:
        return None
    a, b = major[-2], major[-1]
    span = b.price - a.price
    if span == 0:
        return None
    up = span > 0
    levels = {f"fib_{round(r * 1000)}": round(b.price - r * span, 4) for r in rules["fib_retracements"]}
    levels.update({f"fib_ext_{round(e * 1000)}": round(a.price + e * span, 4)
                   for e in rules["fib_extensions"]})
    close = float(bars["close"].iloc[-1])
    return Fibonacci("up" if up else "down", a.i, b.i, a.price, b.price, levels,
                     round((b.price - close) / span, 4), _day(bars, a.i), _day(bars, b.i))
