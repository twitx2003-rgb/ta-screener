"""Levels read from a detection's own geometry, shared by the chart drawings, the
agents' facts and the outcome ledger, so all three say the same thing.

- `detection_key`: one pattern across daily scans: (symbol, pattern, first turning
  point, last turning point). Confirmed turning points never move (pivots.py), so
  the key holds while a breakout day or a line is refitted. Both ends are needed:
  triangles and wedges can report two patterns from the same first pivot, ending
  on different pivots. (The last *point*, not `end`: a forming cup's or flag's end
  moves with every new bar.)
- `invalidation`: the price a close must go beyond, against the breakout, for the
  pattern to count as failed: back beyond the whole pattern. Per family:
    double / triple / head and shoulders, triangles, rectangle, wedges  the lowest
        turning point (upward breakout) or the highest (downward); for the reversals
        this is the detector's "busted" level;
    flag, pennant, high-tight flag  the low (high, for a bear flag) of the
        consolidation after the pole's top;
    cup with handle  the lowest low after the right lip (the handle's low).
  Not the opposite trendline at the breakout: near a triangle's apex that line is
  almost at the breakout level, and an ordinary pullback would count as a failure
  (the first live ledger showed ~90% of triangles "failed" that way).
  A pattern whose direction is not known yet (still forming, two-sided) has none.
"""
from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

import pandas as pd

REVERSALS = frozenset({"double_top", "double_bottom", "triple_top", "triple_bottom",
                       "head_shoulders_top", "head_shoulders_bottom"})
TRENDLINE_PATTERNS = frozenset({"ascending_triangle", "descending_triangle", "symmetrical_triangle",
                                "rectangle", "rising_wedge", "falling_wedge"})
# consolidations measured from the bar after this turning point to the pattern's end
AFTER_POINT = {"flag": 1, "pennant": 1, "high_tight_flag": 1, "cup_with_handle": 2}


def day_of(value: Any) -> date | None:
    """A date from a date, a timestamp or an ISO string (None when missing)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def geometry(record: dict[str, Any], key: str) -> list[dict]:
    """`points` or `lines` of a detection record, parsed or from its *_json column."""
    value = record.get(key)
    if isinstance(value, list):
        return value
    raw = record.get(f"{key}_json")
    return json.loads(raw) if isinstance(raw, str) and raw else []


def detection_key(record: dict[str, Any]) -> str:
    points = geometry(record, "points")
    last = day_of(points[-1]["date"]) if points else day_of(record["end"])
    return f"{record['symbol']}|{record['pattern']}|{day_of(record['start']).isoformat()}|{last.isoformat()}"


def invalidation(record: dict[str, Any], bars: pd.DataFrame | None = None) -> float:
    direction = record.get("direction")
    if direction not in ("bullish", "bearish"):
        return math.nan
    bullish = direction == "bullish"
    pattern = record.get("pattern")
    if pattern in REVERSALS or pattern in TRENDLINE_PATTERNS:
        prices = [float(p["price"]) for p in geometry(record, "points")]
        if not prices:
            return math.nan
        return min(prices) if bullish else max(prices)
    if pattern in AFTER_POINT and bars is not None:
        points = geometry(record, "points")
        anchor = AFTER_POINT[pattern]
        if len(points) <= anchor:
            return math.nan
        after, until = day_of(points[anchor]["date"]), day_of(record.get("end"))
        days = bars["timestamp"].dt.date
        inside = (days > after) & (days <= until)
        segment = bars.loc[inside, "low"] if bullish else bars.loc[inside, "high"]
        if not len(segment):
            return math.nan
        return float(segment.min()) if bullish else float(segment.max())
    return math.nan
