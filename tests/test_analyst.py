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
        assert re.fullmatch(r"(zone|tl|div|pat)_\d+|fib|vp|ma|swings", key), key
    svg = render(bars, result)
    xml.dom.minidom.parseString(svg)
    assert not re.search(r"(?<![a-z])nan(?![a-z])", svg.lower())
    assert render(bars, result, ["ma"]).count('class="ann"') < svg.count('class="ann"')


# ------------------------------------------------------------------ Pine Script
def test_the_pine_script_draws_only_data_it_was_given():
    from tascreen.analyst.pine import _pine_string, pine_script

    knots = [(0, 60)] + [(k, 60 + 0.12 * k + (8 if (k // 20) % 2 else -8)) for k in range(20, 400, 20)]
    bars = from_knots(knots)
    result = analyse(bars, "NYSE:SYN")
    script = pine_script(result, bars)
    assert script.startswith("//@version=6\n") and 'string SYMBOL = "NYSE:SYN"' in script
    days = re.search(r"anchorDays = array\.from\(([^)]*)\)", script).group(1).split(", ")
    closes = re.search(r"anchorCloses = array\.from\(([^)]*)\)", script).group(1).split(", ")
    assert len(days) == len(closes) and all(re.fullmatch(r"20\d{6}", d) for d in days)
    used = {int(n) for n in re.findall(r"array\.get\(anchorBars, (\d+)\)", script)}
    assert used and max(used) < len(days)
    assert "nan" not in script.lower() and script.count("(") == script.count(")")
    assert "box.new" in script and "label.new" in script
    assert "showZones = input.bool(true" in script and "ta.sma" not in script
    assert script.count("if showZones and") <= 4                        # the simple view only
    assert _pine_string('a "b" \\ c\nd') == '"a \\"b\\" \\\\ c d"'
    assert _pine_string("one", 'two "2"') == '"one\\ntwo \\"2\\""'          # Pine's newline escape
    assert "\n" not in _pine_string("a\nb", "c\r\nd")


# ------------------------------------------------------------------ the simple view
def _view_case(close, fib_end=120.0):
    from tascreen.analyst.facts import Analysis

    a = Analysis("TEST:SYN", "2026-01-30", 0)
    a.facts = {"close": {"value": close}, "atr": {"value": 2.0}}

    def zone(lo, hi, kind):
        return {"type": "zone", "kind": kind, "low": lo, "high": hi,
                "first_day": "2026-01-02", "last_day": "2026-01-20", "touches": 2}

    span = fib_end - 90.0
    a.drawings = {"zone_1": zone(129, 130, "resistance"), "zone_2": zone(119, 120, "resistance"),
                  "zone_3": zone(112, 113, "resistance"), "zone_4": zone(104.5, 105.5, "support"),
                  "zone_5": zone(99, 100, "support"), "zone_6": zone(90, 91, "support"),
                  "fib": {"type": "fib", "direction": "up", "start_day": "2026-01-05",
                          "end_day": "2026-01-15", "start": 90.0, "end": fib_end,
                          "levels": {"fib_382": fib_end - 0.382 * span, "fib_500": fib_end - 0.5 * span,
                                     "fib_618": fib_end - 0.618 * span, "fib_ext_1618": 90 + 1.618 * span}},
                  "vp": {"type": "profile", "edges": [90.0, 100.0, 110.0, 120.0],
                         "volume": [1.0, 3.0, 1.0], "poc": 105.0, "val": 100.0, "vah": 110.0},
                  "tl_1": {"type": "line"}, "ma": {"type": "ma", "periods": [50]}}
    return a


def test_the_simple_view_keeps_the_nearest_levels_and_joins_the_ones_that_meet():
    from tascreen.analyst.view import simple_view

    view = simple_view(_view_case(108.0), RULES)
    assert {k for k, v in view.items() if v["type"] == "zone"} == {"zone_2", "zone_3", "zone_4", "zone_5"}
    assert "tl_1" not in view and view["ma"]["periods"] == [50]   # the 50-day average only
    fib = view["fib"]                                   # 108 is 40% back into the 90 -> 120 move
    assert fib["show"] and "fib_ext_1618" not in fib["levels"]
    assert view["zone_4"]["notes"] == ["fib_500", "poc"]    # 105 is on the 104.5-105.5 zone
    assert set(fib["levels"]) == {"fib_382", "fib_618"}
    assert view["vp"]["show"] and not view["vp"]["poc_label"]


def test_the_view_adds_a_near_yearly_extreme_and_a_fresh_breakout_only():
    from tascreen.analyst.view import simple_view

    def fact(v):
        return {"value": v}

    a = _view_case(108.0)
    a.facts.update({"high_52w": fact(116.0), "high_52w_day": fact("2026-01-12"),   # 7% above, no zone
                    "low_52w": fact(80.0), "low_52w_day": fact("2025-06-02"),      # 26% below: too far
                    "pat_1.sessions_since_breakout": fact(4), "pat_2.sessions_since_breakout": fact(60)})
    pattern = {"type": "pattern", "family": "chart", "direction": "bullish", "lines": [], "points": []}
    a.drawings.update({"pat_1": pattern, "pat_2": pattern,
                       "pat_3": {**pattern, "family": "candle"}})
    view = simple_view(a, RULES)
    assert view["hi52"] == {"type": "extreme", "price": 116.0, "label": "שיא שנתי", "day": "2026-01-12"}
    assert "lo52" not in view
    assert "pat_1" in view and "pat_2" not in view and "pat_3" not in view
    a.facts["high_52w"] = fact(119.5)                          # on the drawn 119-120 zone: its label
    assert "hi52" not in simple_view(a, RULES)


def test_fibonacci_and_the_profile_stay_off_when_they_do_not_matter_now():
    from tascreen.analyst.view import simple_view

    view = simple_view(_view_case(128.0), RULES)            # above the swing, far from the POC
    assert not view["fib"]["show"] and not view["vp"]["show"]
    assert all(not v.get("notes") for v in view.values())
    assert set(view["fib"]["levels"]) == {"fib_382", "fib_500", "fib_618"}


def test_the_scenarios_are_built_from_the_zones_the_chart_shows():
    from tascreen.analyst.view import key_level_facts

    facts = {k: v["value"] for k, v in key_level_facts(_view_case(108.0), RULES).items()}
    assert (facts["level.r1.low"], facts["level.r1.high"], facts["level.r2.low"]) == (112, 113, 119)
    assert (facts["level.s1.low"], facts["level.s1.high"], facts["level.s2.high"]) == (104.5, 105.5, 100)
    # a close back through the zone that started a scenario cancels it (review round 1)
    assert (facts["up.trigger"], facts["up.next"], facts["up.cancel"]) == (113, 119, 112)
    assert (facts["down.trigger"], facts["down.next"], facts["down.cancel"]) == (104.5, 100, 105.5)
    assert (facts["up.trigger_pct"], facts["up.next_pct"], facts["down.trigger_pct"]) == (4.6, 10.2, 3.2)
    assert facts["event.kind"] == "position"
    assert facts["position"] == "בין התמיכה להתנגדות, קרוב יותר לתמיכה"      # 2.5 below vs 4 above
    assert facts["level.r1.distance_pct"] == 3.7 and facts["level.s1.distance_pct"] == 2.3
    inside = {k: v["value"] for k, v in key_level_facts(_view_case(105.0), RULES).items()}
    assert inside["position"] == "בתוך אזור התמיכה הקרוב" and inside["level.s1.distance_pct"] == 0
    assert (inside["down.trigger"], inside["down.cancel"], inside["event.kind"]) == (104.5, 105.5, "inside_zone")


def test_near_levels_join_into_a_band_and_the_next_level_is_never_right_behind():
    from tascreen.analyst.facts import Analysis
    from tascreen.analyst.view import key_level_facts

    def zone(lo, hi, kind):
        return {"type": "zone", "kind": kind, "low": lo, "high": hi,
                "first_day": "2026-01-02", "last_day": "2026-01-20", "touches": 2}

    a = Analysis("TEST:SYN", "2026-01-30", 0)
    a.facts = {"close": {"value": 100.0}, "atr": {"value": 2.0}, "high_52w": {"value": 112.0},
               "low_52w": {"value": 80.0}}
    a.drawings = {"zone_1": zone(103, 104, "resistance"), "zone_2": zone(104.8, 105.5, "resistance"),
                  "zone_3": zone(96, 97, "support"), "zone_4": zone(95.4, 95.9, "support"),
                  "swings": {"type": "swings", "points": [{"day": "2026-01-10", "price": 90.0, "kind": "L"}]}}
    f = {k: v["value"] for k, v in key_level_facts(a, RULES).items()}
    assert (f["up.trigger"], f["up.cancel"]) == (105.5, 103)          # 103-104 and 104.8-105.5: one band
    assert (f["up.next"], f["up.next_what"]) == (112, "השיא השנתי")
    assert (f["down.trigger"], f["down.next"]) == (95.4, 90)          # the swing low of 10/01
    assert f["down.next_what"] == "השפל מ-10/01"
