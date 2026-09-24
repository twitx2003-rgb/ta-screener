"""The chart analyst: a technical analysis of one stock on request (owner's idea,
2026-09-24; the owner asks through the Telegram bot).

The engine (this package) computes everything that is drawn or quoted: support and
resistance zones, trendlines, Fibonacci levels, indicators and divergences, the volume
profile, and the Bulkowski patterns found by the scanner's own detectors. Each object
gets an id (zone_2, tl_1, fib_618, div_1, pat_1); a language model later explains them
and chooses which to show, but never invents a level.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError

RULES_PATH = Path(__file__).with_name("rules.yaml")


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    """name -> value (the file keeps each value's origin and note next to it)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict) or "value" not in entry:
            raise ConfigError(f"analyst rules: '{name}' needs a value")
        out[name] = entry["value"]
    return out
