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
    bands = {k: (v["low"], v["high"]) for k, v in view.items() if v["type"] == "zone"}
    assert bands == {"lvl_r1": (112, 113), "lvl_r2": (119, 120), "lvl_s1": (104.5, 105.5), "lvl_s2": (99, 100)}
    assert "tl_1" not in view and view["ma"]["periods"] == [50, 150, 200]   # all three the text names
    fib = view["fib"]                                   # 108 is 40% back into the 90 -> 120 move
    assert fib["show"] and "fib_ext_1618" not in fib["levels"]
    assert view["lvl_s1"]["notes"] == ["fib_500", "poc"]    # 105 is on the 104.5-105.5 zone
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
    # the yearly high is a level of the map: the second band above, drawn as one price
    assert (view["lvl_r2"]["low"], view["lvl_r2"]["high"], view["lvl_r2"]["label"]) == (116.0, 116.0, "שיא שנתי")
    assert "lo52" not in view and "hi52" not in view
    assert "pat_1" in view and "pat_2" not in view and "pat_3" not in view
    a.facts["high_52w"] = fact(119.5)                          # on the 119-120 zone: named in its label
    view = simple_view(a, RULES)
    assert view["lvl_r2"]["notes"] == ["hi52"] and (view["lvl_r2"]["low"], view["lvl_r2"]["high"]) == (119, 120)


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
    assert facts["level.r1.what"] == "אזור ההתנגדות"
    # a close back into the zone it crossed cancels it: a failed break (review round 3: a
    # cancel 1 ATR back sat on the far side of the close)
    assert (facts["up.trigger"], facts["up.next"], facts["up.cancel"]) == (113, 119, 112)
    assert (facts["down.trigger"], facts["down.next"], facts["down.cancel"]) == (104.5, 100, 105.5)
    assert facts["up.cancel_what"] == "חזרה אל תוך האזור"
    assert (facts["up.trigger_pct"], facts["up.next_pct"], facts["down.trigger_pct"]) == (4.6, 10.2, 3.2)
    assert (facts["up.risk_pct"], facts["up.room_pct"], facts["up.next_far"]) == (0.9, 5.3, 120)
    assert facts["up.trigger_what"] == "הקצה העליון של ההתנגדות הקרובה"
    assert facts["up.next_what"] == "ההתנגדות הבאה"
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


def _event_case(**facts):
    from tascreen.analyst.view import key_event

    rules = {**RULES, "event_fresh_sessions": 10}
    return key_event({k: {"value": v} for k, v in facts.items()}, "בין התמיכה להתנגדות", rules)


def test_the_key_event_names_what_happened_this_week():
    # a zone the price just closed through, with the new high it made (review round 2)
    zone = _event_case(**{"zone_2.broken": "נפרץ כלפי מעלה (התנגדות שהפכה לתמיכה)", "zone_2.low": 50.0,
                          "zone_2.high": 52.0, "zone_2.broken_day": "2026-03-16",
                          "zone_2.broken_sessions_ago": 3, "high_52w_sessions_ago": 0,
                          "high_52w_day": "2026-03-19", "high_52w": 60.0})
    assert zone == ("המחיר פרץ מעל אזור ההתנגדות שבין 50.00 ל-52.00 ב-16/03, ורשם שיא שנתי חדש היום",
                    "zone_break")
    # a new 52-week low today, on a day that closed up
    low = _event_case(low_52w_sessions_ago=0, low_52w_day="2026-03-19", low_52w=40.0,
                      low_52w_distance_pct=2.8, change_1d_pct=2.0)
    assert low == ("היום נגע בשפל שנתי חדש (40.00) ונסגר 2.8% מעליו, בעלייה של 2.0%", "new_low")
    # a retest of the broken line from below is not a failure
    retest = _event_case(**{"pat_1.state": "המחיר חזר לבדוק את קו השבירה מלמטה", "pat_1.name": "טריז עולה",
                            "pat_1.direction": "דובי", "pat_1.breakout_day": "2026-03-10",
                            "pat_1.sessions_since_breakout": 9, "pat_1.line_now": 120.0})
    assert retest == ("אחרי השבירה מתבנית טריז עולה ב-10/03, המחיר חזר לבדוק את קו השבירה (120.00) מלמטה",
                      "retest")
    fresh = _event_case(**{"pat_1.state": "המחיר מעל קו הפריצה", "pat_1.name": "משולש עולה",
                           "pat_1.direction": "שורי", "pat_1.breakout_day": "2026-03-19",
                           "pat_1.sessions_since_breakout": 0, "pat_1.close_vs_breakout_pct": 1.3})
    assert fresh == ("המחיר נסגר היום לראשונה מעל קו הפריצה של תבנית משולש עולה", "fresh_breakout")
    later = _event_case(**{"pat_1.state": "המחיר מעל קו הפריצה", "pat_1.name": "משולש עולה",
                           "pat_1.direction": "שורי", "pat_1.breakout_day": "2026-03-16",
                           "pat_1.sessions_since_breakout": 3, "pat_1.close_vs_breakout_pct": 1.3})
    assert later == ("פריצה כלפי מעלה מתבנית משולש עולה ב-16/03; המחיר 1.3% מעל קו הפריצה", "fresh_breakout")
    loud = _event_case(**{"pat_1.state": "המחיר מעל קו הפריצה", "pat_1.name": "משולש עולה",
                          "pat_1.direction": "שורי", "pat_1.breakout_day": "2026-03-16",
                          "pat_1.sessions_since_breakout": 3, "pat_1.close_vs_breakout_pct": 1.3,
                          "pat_1.breakout_volume_ratio": 2.04})
    assert loud[0] == "פריצה כלפי מעלה מתבנית משולש עולה ב-16/03, בנפח גבוה פי 2.0 מהממוצע; המחיר 1.3% מעל קו הפריצה"
    failed = _event_case(**{"pat_2.state": "התבנית נכשלה: המחיר עבר את רמת הביטול שלה (95.00)",
                            "pat_2.name": "משולש סימטרי", "pat_2.direction": "דובי",
                            "pat_2.breakout_day": "2026-03-09", "pat_2.sessions_since_breakout": 8})
    assert failed[1] == "failed_pattern" and failed[0].startswith("תבנית משולש סימטרי נכשלה")
    # turned back at a zone today by more than an ATR
    rejected = _event_case(**{"zone_3.low": 10.0, "zone_3.high": 11.0, "close": 9.5,
                              "change_1d_pct": -7.8, "atr": 0.3})
    assert rejected == ("המחיר נבלם היום באזור ההתנגדות שבין 10.00 ל-11.00 וירד 7.8%", "rejected")
    # an uptrend back at its rising 50-day average; a range
    pullback = _event_case(**{"sma50.direction": "עולה", "sma50.distance_atr": 0.6, "sma50": 50.0,
                              "vs_sma50_pct": 0.9, "high_52w_distance_pct": 4.8})
    assert pullback == ("המחיר חזר אל ממוצע 50 יום העולה (50.00, 0.9% מתחת לסגירה), "
                        "4.8% מתחת לשיא השנתי", "pullback_ma50")
    ranged = _event_case(**{"range.low": 30.0, "range.high": 36.0, "range.sessions": 120})
    assert ranged[1] == "range" and ranged[0].startswith("המחיר מדשדש בין 30.00 ל-36.00 ב-6 החודשים")


def test_with_nothing_past_the_trigger_a_fresh_pattern_target_is_the_next_level():
    from tascreen.analyst.facts import Analysis
    from tascreen.analyst.view import key_level_facts

    a = Analysis("TEST:SYN", "2026-01-30", 0)
    a.facts = {"close": {"value": 51.0}, "atr": {"value": 2.0}, "high_52w": {"value": 52.0},
               "low_52w": {"value": 30.0}, "pat_1.target": {"value": 60.0}, "pat_1.direction": {"value": "שורי"},
               "pat_1.sessions_since_breakout": {"value": 0}}
    a.drawings = {"zone_1": {"type": "zone", "kind": "support", "low": 49.0, "high": 50.0,
                             "first_day": "2026-01-02", "last_day": "2026-01-20", "touches": 3}}
    f = {k: v["value"] for k, v in key_level_facts(a, RULES).items()}
    assert (f["up.trigger"], f["up.next"], f["up.next_what"]) == (52.0, 60.0, "יעד לפי גובה התבנית")
    # a one-price trigger: half a day's range back, never the trigger itself (review round 2)
    # nor a level past the close (round 3)
    assert f["up.cancel"] == 51.0 and f["up.cancel_what"] == "חצי מהתנודה היומית הממוצעת"
    assert "up.no_next" not in f
    a.facts.pop("pat_1.target")
    f = {k: v["value"] for k, v in key_level_facts(a, RULES).items()}
    assert f["up.no_next"] == "מעליו אין רמות מהשנה האחרונה"


def _map_case(close, zones, **facts):
    from tascreen.analyst.facts import Analysis

    a = Analysis("TEST:SYN", "2026-01-30", 0)
    a.facts = {"close": {"value": close}, "atr": {"value": 2.0}, **{k: {"value": v} for k, v in facts.items()}}
    a.drawings = {f"zone_{n}": {"type": "zone", "kind": kind, "low": lo, "high": hi, "first_day": "2026-01-02",
                                "last_day": "2026-01-20", "touches": 2}
                  for n, (lo, hi, kind) in enumerate(zones, 1)}
    return a


def test_the_levels_line_the_scenarios_and_the_chart_share_one_map():
    from tascreen.analyst.view import key_level_facts, simple_view

    # four touching support zones: one band only up to 1.5 ATR wide (review round 3: an
    # unbounded chain made a "zone" three ATRs wide, and the scenario started at its far end)
    a = _map_case(100.0, [(97.6, 98.6, "support"), (96.6, 97.4, "support"), (95.4, 96.2, "support"),
                          (94.2, 95.0, "support"), (104.0, 105.0, "resistance")])
    f = {k: v["value"] for k, v in key_level_facts(a, RULES).items()}
    assert (f["level.s1.low"], f["level.s1.high"], f["level.s2.low"], f["level.s2.high"]) == (96.6, 98.6, 94.2, 96.2)
    assert f["down.trigger"] == f["level.s1.low"]               # the band the levels line names
    assert f["down.next"] == f["level.s2.high"]                 # the next band's own edges
    view = simple_view(a, RULES)
    assert (view["lvl_s1"]["low"], view["lvl_s1"]["high"]) == (f["level.s1.low"], f["level.s1.high"])
    assert (view["lvl_s2"]["low"], view["lvl_s2"]["high"]) == (f["down.next_far"], f["down.next"])
    # a cancel never on the far side of the close
    assert f["up.cancel"] >= 100.0 and f["down.cancel"] <= 100.0


def test_right_after_a_breakout_the_scenario_is_that_the_breakout_holds():
    from tascreen.analyst.view import key_level_facts

    a = _map_case(51.0, [(49.0, 50.0, "support")], high_52w=53.0, low_52w=30.0,
                  **{"pat_1.name": "משולש עולה", "pat_1.direction": "שורי", "pat_1.state": "המחיר מעל קו הפריצה",
                     "pat_1.sessions_since_breakout": 1, "pat_1.line_now": 50.2})
    f = {k: v["value"] for k, v in key_level_facts(a, RULES).items()}
    assert (f["up.hold"], f["up.hold_pattern"], f["up.next"]) == (50.2, "משולש עולה", 53.0)
    assert "up.trigger" not in f and "up.cancel" not in f
    assert f["level.s1.includes"] == "קו התבנית"                  # the broken line sits in the support


def test_a_crossed_trendline_is_not_drawn_and_every_scenario_level_is():
    from tascreen.analyst.view import simple_view

    a = _map_case(100.0, [(96.0, 97.0, "support")], high_52w=101.5, low_52w=60.0,
                  **{"fib.direction": "עלייה", "fib_ext_1272": 106.0})
    a.drawings["tl_1"] = {"type": "line", "kind": "resistance", "day1": "2026-01-02", "price1": 90.0,
                          "day2": "2026-01-30", "price2": 99.0, "touches": 4}      # under the price: crossed
    view = simple_view(a, RULES)
    assert "tl_1" not in view
    assert view["up_next"] == {"type": "level", "price": 106.0, "label": "פיבונאצ׳י 127.2%", "kind": "resistance"}


def test_volume_is_told_in_words():
    from tascreen.analyst.facts import volume_words

    assert volume_words(2.04) == "גבוה, פי 2.0 מהממוצע"
    assert volume_words(1.02) == "רגיל, סביב הממוצע"
    assert volume_words(0.7) == "נמוך, כ-30% מתחת לממוצע"
    assert volume_words(float("nan")) is None


def test_labels_at_one_price_share_one_number():
    from tascreen.analyst.chart import _merge

    assert _merge("שפל שנתי \u206640.25\u2069", "יעד התבנית \u206640.25\u2069") == "שפל שנתי ויעד התבנית \u206640.25\u2069"
    assert _merge("שפל שנתי \u206640.25\u2069", "יעד \u206640.30\u2069") == "שפל שנתי \u206640.25\u2069\nיעד \u206640.30\u2069"
