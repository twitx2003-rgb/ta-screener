"""Levels drawn from turning points: support/resistance zones, trendlines, Fibonacci.

- Zones: turning points (pivots.zigzag at `minor_pivot_atr`) whose prices lie within
  `zone_merge_atr` ATRs of each other are one zone; a zone tested at least
  `zone_min_touches` times is kept, the nearest `zones_each_side` above and below the
  last close. Above the close it is resistance, below it support.
- Trendlines: along the edge of the wicks, the way one is drawn by hand: from a turning
  point of the kind (a low for support, a high for resistance), each edge of the lows'
  (highs') convex hull from it to a few sessions ago is a line through two wicks that no
  wick goes through. A touch is a wick that is a local extreme within `trendline_touch_atr`
  ATRs of the line (bars this close together count once); at least `trendline_min_touches`.
  The last FRESH_TEST sessions may test it with a wick, not close more than
  `trendline_break_atr` ATRs through it. Last touched within `trendline_recent_sessions`.
  The best one per kind: most touches, then longest. (Owner, 2026-10-10: lines "that do
  not hit the candles exactly": through two turning points, a third touch could sit a third
  of an ATR off the line and candles crossed it.)
- Fibonacci: the last completed major swing (pivots at `major_pivot_atr`): retracement
  levels back from its end, extensions beyond it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..patterns.chart import edge_lines
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


TOUCH_GAP = 3                  # wicks this many bars apart or closer are one touch
FRESH_TEST = 3                 # the last sessions may be testing the line with a wick


def _local_extremes(values: np.ndarray, low: bool, reach: int = 2) -> np.ndarray:
    """Bars whose low (high) is the lowest (highest) within `reach` bars on each side."""
    n = len(values)
    out = np.zeros(n, bool)
    for i in range(n):
        window = values[max(0, i - reach):min(n, i + reach + 1)]
        out[i] = values[i] == (np.nanmin(window) if low else np.nanmax(window))
    return out


def _best_line(bars: pd.DataFrame, points: list[Pivot], atr: np.ndarray, kind: str,
               rules: dict[str, Any]) -> Trendline | None:
    support = kind == "support"
    closes = bars["close"].to_numpy(float)
    tips = bars["low" if support else "high"].to_numpy(float)
    extreme = _local_extremes(tips, support)
    last = len(bars) - 1
    settled = last - FRESH_TEST
    unit = np.nan_to_num(atr, nan=float(np.nanmedian(atr)))
    tried: set[tuple[float, float]] = set()
    best = None
    for p in points:
        if p.i >= settled:
            continue
        for a, b in edge_lines(tips, p.i, settled, upper=not support):
            if (round(a, 6), round(b, 9)) in tried:
                continue
            tried.add((round(a, 6), round(b, 9)))
            line = a + b * np.arange(p.i, last + 1)
            gap = np.abs(tips[p.i:] - line) / unit[p.i:]
            near = np.flatnonzero(extreme[p.i:] & (gap <= rules["trendline_touch_atr"])) + p.i
            touching = [int(i) for k, i in enumerate(near) if k == 0 or i - near[k - 1] > TOUCH_GAP]
            if len(touching) < rules["trendline_min_touches"]:
                continue
            through = ((line - closes[p.i:]) if support else (closes[p.i:] - line)) / unit[p.i:]
            if (through[settled - p.i + 1:] > rules["trendline_break_atr"]).any():
                continue                        # closed through lately: not a live line
            last_touch = int(near[-1])
            if last - last_touch > rules["trendline_recent_sessions"]:
                continue
            if abs(a + b * last - closes[last]) > rules["trendline_max_distance_atr"] * unit[last]:
                continue
            key = (len(touching), last_touch - touching[0])
            if best is None or key > best[0]:
                best = (key, Trendline("", kind, touching[0], touching[1], a, b, len(touching), last_touch,
                                        _day(bars, touching[0]), _day(bars, touching[1])))
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


def fibonacci(bars: pd.DataFrame, major: list[Pivot], rules: dict[str, Any],
              atr_now: float = math.nan) -> Fibonacci | None:
    """The retracements of the latest big move. That is the leg still in progress when it
    is already a big move (from the last major turning point to the extreme after it: a
    zigzag confirms a turning point only after the reversal, so a strong recent move has
    no confirmed end yet); otherwise the last confirmed leg. None for a move smaller than
    fib_min_atr ATRs or shorter than fib_min_bars sessions (review round 1: a two-day
    drop and a leg the price had already rebuilt were measured)."""
    if len(major) < 2:
        return None
    a, b = major[-2], major[-1]
    big = rules["major_pivot_atr"] * atr_now if math.isfinite(atr_now) else math.inf
    after = slice(b.i + 1, len(bars))
    if b.i + 1 < len(bars):
        if b.kind == "H":
            k = int(np.argmin(bars["low"].to_numpy(float)[after])) + b.i + 1
            end = Pivot(k, float(bars["low"].iloc[k]), "L")
        else:
            k = int(np.argmax(bars["high"].to_numpy(float)[after])) + b.i + 1
            end = Pivot(k, float(bars["high"].iloc[k]), "H")
        if abs(end.price - b.price) >= big and end.i - b.i >= rules["fib_min_bars"]:
            a, b = b, end
    span = b.price - a.price
    if span == 0 or b.i - a.i < rules["fib_min_bars"]:
        return None
    if math.isfinite(atr_now) and abs(span) < rules["fib_min_atr"] * atr_now:
        return None
    up = span > 0
    levels = {f"fib_{round(r * 1000)}": round(b.price - r * span, 4) for r in rules["fib_retracements"]}
    levels.update({f"fib_ext_{round(e * 1000)}": round(a.price + e * span, 4)
                   for e in rules["fib_extensions"]})
    close = float(bars["close"].iloc[-1])
    return Fibonacci("up" if up else "down", a.i, b.i, a.price, b.price, levels,
                     round((b.price - close) / span, 4), _day(bars, a.i), _day(bars, b.i))
