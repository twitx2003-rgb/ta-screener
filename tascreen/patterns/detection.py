"""What a detector reports: one Detection with a checklist of its rules.

Every rule a detection passed is recorded with the value measured and the
threshold from rules.yaml, so the website (and the pattern-verifier agent) can
show why a pattern was recognised, not just that it was.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

STATUSES = ("forming", "breakout", "busted", "signal")


@dataclass
class Check:
    rule: str                     # parameter or rule name
    label_he: str
    value: Any
    threshold: Any
    passed: bool
    origin: str = ""              # site | ours | "" (a structural rule)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("value", "threshold", "passed"):
            value = out[key]
            if hasattr(value, "item"):            # numpy scalar -> Python (JSON would stringify it)
                value = value.item()
            if isinstance(value, float):
                value = None if math.isnan(value) else round(value, 4)
            out[key] = value
        return out


@dataclass
class Detection:
    symbol: str
    family: str                   # chart | candle
    pattern: str                  # key in rules.yaml
    direction: str                # bullish | bearish | either (still forming)
    status: str                   # forming | breakout | busted | signal (candles)
    start: pd.Timestamp
    end: pd.Timestamp             # last turning point / last candle of the pattern
    breakout_date: pd.Timestamp | None = None
    breakout_price: float = math.nan
    height: float = math.nan
    target: float = math.nan      # classic full-height measure rule — not a forecast
    volume_trend: str = ""        # down | up | "" — informational, not a rule
    points: list[dict] = field(default_factory=list)    # {date, price, label}
    lines: list[dict] = field(default_factory=list)     # {x1, y1, x2, y2, label}
    checks: list[Check] = field(default_factory=list)

    def row(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "family": self.family, "pattern": self.pattern,
            "direction": self.direction, "status": self.status,
            "start": self.start, "end": self.end,
            "breakout_date": self.breakout_date,
            "breakout_price": self.breakout_price, "height": self.height,
            "target": self.target, "volume_trend": self.volume_trend,
            "points_json": json.dumps(self.points, default=str),
            "lines_json": json.dumps(self.lines, default=str),
            "checks_json": json.dumps([c.as_dict() for c in self.checks], ensure_ascii=False,
                                      default=str),
        }


def pct_diff(a: float, b: float) -> float:
    """|a - b| as a percent of the smaller of the two."""
    return abs(a - b) / min(abs(a), abs(b)) * 100


def volume_trend(volume: pd.Series) -> str:
    """Sign of a least-squares slope of volume across the pattern."""
    y = volume.to_numpy(dtype=float)
    if len(y) < 3 or not y.any():
        return ""
    x = range(len(y))
    mean_x, mean_y = (len(y) - 1) / 2, y.mean()
    slope = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    return "down" if slope < 0 else "up"
