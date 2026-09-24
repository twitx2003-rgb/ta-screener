"""The chart analyst's engine on synthetic shapes (made-up prices)."""
from __future__ import annotations

import json
import re
import xml.dom.minidom

import numpy as np
import pandas as pd
import pytest

from synth import frame, from_knots
from tascreen.analyst import load_rules
from tascreen.analyst.chart import render
from tascreen.analyst.facts import analyse
from tascreen.analyst.signals import divergences, macd, rsi_series, volume_profile
from tascreen.analyst.zones import fibonacci, pivots, sr_zones, trendlines
from tascreen.indicators import atr as atr_series

RULES = load_rules()


def _atr(bars):
    return atr_series(bars, 14).to_numpy()


RANGE = [(0, 105)] + [(10 * k, 110 if k % 2 else 100) for k in range(1, 12)] + [(125, 105)]


def test_a_level_tested_again_and_again_is_a_zone():
    bars = from_knots(RANGE)
    atr = _atr(bars)
    zones = sr_zones(bars, pivots(bars, atr, RULES["minor_pivot_atr"]), float(atr[-1]), RULES)
    top = [z for z in zones if z.kind == "resistance"]
    bottom = [z for z in zones if z.kind == "support"]
    assert top and top[-1].low <= 110.5 <= top[-1].high + 0.5 and top[-1].touches >= 3
    assert bottom and bottom[0].low - 0.5 <= 99.5 <= bottom[0].high and bottom[0].touches >= 3
    assert [z.id for z in zones] == [f"zone_{n}" for n in range(1, len(zones) + 1)]


RISING = [(0, 100), (10, 110), (20, 104), (30, 114), (40, 108), (50, 118), (60, 112),
          (70, 122), (80, 116), (90, 124)]


def test_rising_lows_make_a_support_line_until_a_close_breaks_it():
    bars = from_knots(RISING)
    atr = _atr(bars)
    lines = trendlines(bars, pivots(bars, atr, RULES["minor_pivot_atr"]), atr, RULES)
    support = [t for t in lines if t.kind == "support"]
    assert support and support[0].touches >= 3 and support[0].b > 0
    broken = from_knots(RISING + [(96, 100), (100, 99)])
    atr = _atr(broken)
    lines = trendlines(broken, pivots(broken, atr, RULES["minor_pivot_atr"]), atr, RULES)
    assert not [t for t in lines if t.kind == "support" and t.i1 < 60]


def test_fibonacci_of_the_last_major_swing():
    bars = from_knots([(0, 110), (20, 100), (60, 150), (75, 130)], noise=0.0, wiggle=0.0)
    atr = _atr(bars)
    fib = fibonacci(bars, pivots(bars, atr, RULES["major_pivot_atr"]), RULES)
    assert fib.direction == "up" and fib.start == pytest.approx(100) and fib.end == pytest.approx(150)
    assert fib.levels["fib_618"] == pytest.approx(150 - 0.618 * 50)
    assert fib.levels["fib_ext_1618"] == pytest.approx(100 + 1.618 * 50)
    assert fib.position == pytest.approx(0.4, abs=0.01)                 # back at 130


def test_a_weaker_second_high_is_a_bearish_divergence():
    knots = [(0, 100), (40, 100), (50, 125), (62, 112), (90, 127), (100, 118)]
    bars = from_knots(knots, noise=0.05, wiggle=0.2)
    atr = _atr(bars)
    close = bars["close"]
    line, _, _ = macd(close, *RULES["macd"])
    found = divergences(bars, pivots(bars, atr, RULES["minor_pivot_atr"]), rsi_series(close, 14), line, 25)
    assert any(d.kind == "bearish" and d.indicator == "rsi" for d in found)
    assert all(d.id.startswith("div_") for d in found)


def test_the_volume_profile_centres_on_the_busiest_price():
    rows = [(99.5, 100.5, 99, 100)] * 30 + [(119.5, 120.5, 119, 120)] * 30 + [(139, 141, 138, 140)] * 10
    volume = [1e6] * 30 + [5e6] * 30 + [1e6] * 10
    profile = volume_profile(frame(rows, volume=volume), 40, 0.7)
    assert abs(profile["poc"] - 120) < 1.5
    assert profile["val"] <= 120 <= profile["vah"]


def test_the_whole_analysis_is_clean_and_drawable():
    knots = [(0, 60)] + [(k, 60 + 0.12 * k + (8 if (k // 20) % 2 else -8)) for k in range(20, 400, 20)]
    bars = from_knots(knots)
    result = analyse(bars, "TEST:SYN")
    assert result.facts["close"]["unit"] == "$" and "ma.stack" in result.facts
    text = json.dumps(result.facts, ensure_ascii=False)
    assert "nan" not in text.lower() and "none" not in text.lower()
    for key in result.drawings:
        assert re.fullmatch(r"(zone|tl|div|pat)_\d+|fib|vp|ma", key), key
    svg = render(bars, result)
    xml.dom.minidom.parseString(svg)
    assert not re.search(r"(?<![a-z])nan(?![a-z])", svg.lower())
    assert render(bars, result, ["ma"]).count('class="ann"') < svg.count('class="ann"')
