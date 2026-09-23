"""Chart-pattern detectors on synthetic textbook shapes (made-up prices)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from synth import from_knots
from tascreen.patterns.chart import detect_chart
from tascreen.patterns.pivots import zigzag
from tascreen.patterns.rules import load_rules

RULES = load_rules()


def detect(bars):
    return detect_chart(bars, "TEST:SYN", RULES)


def one(bars, pattern):
    hits = [d for d in detect(bars) if d.pattern == pattern]
    assert hits, f"no {pattern}; found {[(d.pattern, d.status) for d in detect(bars)]}"
    return hits[0]


# ------------------------------------------------------------------ pivots
def test_zigzag_alternates_and_needs_a_real_reversal():
    h = np.array([10, 11, 12, 13, 12, 11, 10, 11, 12, 13, 14], float)
    l = h - 0.5
    atr = np.full(len(h), 1.0)
    piv = zigzag(h, l, atr, 2.0)
    # the first bar is a low once price has risen two ATRs off it
    assert [(p.kind, p.i) for p in piv] == [("L", 0), ("H", 3), ("L", 6)]
    assert [p.kind for p in zigzag(h, l, atr, 5.0)] == []      # no swing of 5 ATRs


# ------------------------------------------------------------ double bottom
DOUBLE_BOTTOM = [(0, 130), (80, 100), (95, 112), (110, 100.5), (125, 113.5), (130, 115)]


def test_double_bottom_with_breakout():
    det = one(from_knots(DOUBLE_BOTTOM), "double_bottom")
    assert det.status == "breakout" and det.direction == "bullish"
    assert det.breakout_price == pytest.approx(112 + 0.3, abs=0.2)     # the peak's high
    assert det.target > det.breakout_price
    checks = {c["rule"]: c for c in json.loads(det.row()["checks_json"])}
    assert checks["min_rise_between_pct"]["origin"] == "site"
    assert checks["separation"]["passed"] and checks["confirmation"]["passed"]


def test_double_bottom_forming_before_confirmation():
    det = one(from_knots(DOUBLE_BOTTOM[:4] + [(115, 105)]), "double_bottom")
    assert det.status == "forming" and det.breakout_date is None


def test_bottoms_too_far_apart_in_price_are_not_a_double_bottom():
    bars = from_knots([(0, 130), (80, 100), (95, 115), (110, 106), (125, 118), (130, 119)])
    assert "double_bottom" not in {d.pattern for d in detect(bars)}


def test_a_double_bottom_that_breaks_its_low_first_is_dropped():
    bars = from_knots(DOUBLE_BOTTOM[:4] + [(115, 104), (125, 90), (130, 89)])
    assert "double_bottom" not in {d.pattern for d in detect(bars)}


def test_no_prior_decline_no_double_bottom():
    bars = from_knots([(0, 80), (80, 100), (95, 112), (110, 100.5), (125, 113.5), (130, 115)])
    assert "double_bottom" not in {d.pattern for d in detect(bars)}


# ---------------------------------------------------------- head and shoulders
HS_TOP = [(0, 55), (90, 100), (105, 92), (120, 110), (135, 92.5), (150, 100.5), (165, 86), (168, 85)]


def test_head_and_shoulders_top_breaks_its_neckline():
    det = one(from_knots(HS_TOP), "head_shoulders_top")
    assert det.status == "breakout" and det.direction == "bearish"
    assert [p["label"] for p in det.points] == ["כתף שמאל", "בית שחי", "ראש", "בית שחי", "כתף ימין"]
    assert det.target < det.breakout_price
    assert json.loads(det.row()["lines_json"])[0]["label"] == "קו צוואר"


def test_lopsided_shoulders_are_not_head_and_shoulders():
    bars = from_knots([(0, 55), (90, 100), (105, 92), (120, 110), (135, 92.5), (150, 106),
                       (165, 86), (168, 85)])
    assert "head_shoulders_top" not in {d.pattern for d in detect(bars)}


def test_inverse_head_and_shoulders():
    knots = [(x, 200 - y) for x, y in HS_TOP]
    det = one(from_knots(knots), "head_shoulders_bottom")
    assert det.direction == "bullish" and det.status == "breakout"


# ------------------------------------------------------------------ triangle
ASC_TRIANGLE = [(0, 80), (70, 95), (80, 100), (90, 90), (100, 100), (110, 93), (120, 100),
                (130, 96), (140, 104), (143, 105)]


def test_ascending_triangle_breaks_out_upward():
    det = one(from_knots(ASC_TRIANGLE), "ascending_triangle")
    assert det.status == "breakout" and det.direction == "bullish"
    touches = {c["rule"]: c for c in json.loads(det.row()["checks_json"])}
    assert touches["min_touches_major"]["value"] >= 3
    assert len(json.loads(det.row()["lines_json"])) == 2


def test_triangle_still_inside_its_lines_is_forming_with_no_direction():
    det = one(from_knots(ASC_TRIANGLE[:8] + [(135, 98.5)]), "ascending_triangle")
    assert det.status == "forming" and det.direction == "either"


def test_rising_wedge_breaks_down():
    knots = [(0, 60), (70, 80), (80, 90), (90, 82), (100, 93), (110, 87), (120, 95), (130, 91),
             (140, 86), (143, 85)]
    det = one(from_knots(knots), "rising_wedge")
    assert det.status == "breakout" and det.direction == "bearish"
    checks = {c["rule"]: c for c in json.loads(det.row()["checks_json"])}
    assert checks["converging"]["value"] <= 0.75


def test_a_rising_channel_is_not_a_rising_wedge():
    # both lines rise at the same pace: parallel, never meeting (the live false positive)
    knots = [(0, 60), (70, 80), (80, 86), (90, 80), (100, 90), (110, 84), (120, 94), (130, 88),
             (140, 98), (150, 92), (160, 85), (163, 84)]
    assert "rising_wedge" not in {d.pattern for d in detect(from_knots(knots))}


def test_descending_triangle_by_mirror():
    knots = [(x, 200 - y) for x, y in ASC_TRIANGLE]
    det = one(from_knots(knots), "descending_triangle")
    assert det.direction == "bearish"


# ---------------------------------------------------------------- cup/handle
CUP = [(0, 60), (70, 100), (85, 85), (100, 80), (115, 80), (130, 85), (145, 99.5), (150, 95),
       (155, 96), (160, 103), (163, 104)]


def test_cup_with_handle():
    det = one(from_knots(CUP), "cup_with_handle")
    assert det.status == "breakout" and det.direction == "bullish"
    checks = {c["rule"]: c for c in json.loads(det.row()["checks_json"])}
    assert checks["min_bottom_share"]["passed"] and checks["handle_upper_half"]["passed"]


def test_v_shaped_cup_is_rejected():
    v = [(0, 60), (70, 100), (107, 80), (145, 99.5), (150, 95), (155, 96), (160, 103), (163, 104)]
    assert "cup_with_handle" not in {d.pattern for d in detect(from_knots(v))}


# -------------------------------------------------------------------- flag
def test_bull_flag_after_a_steep_pole():
    knots = [(0, 50), (60, 50), (66, 60), (68, 58.2), (70, 59.4), (72, 57.6), (74, 58.8),
             (76, 57.2), (78, 62), (79, 62.5)]
    det = one(from_knots(knots, noise=0.02, wiggle=0.15), "flag")
    assert det.status == "breakout" and det.direction == "bullish"


# --------------------------------------------------------------- stability
def test_a_steady_trend_produces_no_reversal_patterns():
    rng = np.random.default_rng(3)
    knots = [(0, 50.0)] + [(i, 50 + 0.4 * i + float(rng.normal(0, 0.2))) for i in range(10, 300, 10)]
    found = {d.pattern for d in detect(from_knots(knots))}
    assert not found & {"double_bottom", "double_top", "head_shoulders_top",
                        "head_shoulders_bottom", "triple_top", "triple_bottom"}


def test_every_detection_is_json_ready():
    for det in detect(from_knots(HS_TOP)) + detect(from_knots(CUP)):
        row = det.row()
        json.loads(row["checks_json"]), json.loads(row["points_json"]), json.loads(row["lines_json"])
