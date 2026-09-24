"""The chart analyst: a technical analysis of one stock on request (owner's idea,
2026-09-24; the owner asks through the Telegram bot).

The engine (this package) computes everything that is drawn or quoted: support and
resistance zones, trendlines, Fibonacci levels, indicators and divergences, the volume
profile, and the Bulkowski patterns found by the scanner's own detectors. Each object
gets an id (zone_2, tl_1, fib_618, div_1, pat_1); a language model later explains them
and chooses which to show, but never invents a level.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError

RULES_PATH = Path(__file__).with_name("rules.yaml")
SYMBOL_TEXT = re.compile(r"(?:([A-Z]{2,8}):)?([A-Z][A-Z0-9.\-]{0,9})")


def find_symbol(bars_dir: Path, text: str) -> str:
    """What someone typed ('nvda', '$NVDA', 'NASDAQ:NVDA') -> the stored symbol."""
    typed = text.strip().upper().lstrip("$")
    match = SYMBOL_TEXT.fullmatch(typed)
    if not match:
        raise ConfigError(f"'{text.strip()[:20]}' is not a stock symbol")
    exchange, ticker = match.groups()
    pattern = f"{exchange}_{ticker}.parquet" if exchange else f"*_{ticker}.parquet"
    found = sorted(p.stem for p in bars_dir.glob(pattern))
    if len(found) != 1:
        raise ConfigError(f"{typed}: " + ("not in the list of stocks" if not found
                                          else f"more than one match ({', '.join(found)})"))
    return found[0].replace("_", ":", 1)


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    """name -> value (the file keeps each value's origin and note next to it)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict) or "value" not in entry:
            raise ConfigError(f"analyst rules: '{name}' needs a value")
        out[name] = entry["value"]
    return out
