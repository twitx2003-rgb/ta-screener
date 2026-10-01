"""The breakout chart (tascreen/chart_svg.py) on synthetic textbook shapes (made-up prices):
its geometry helpers, and that each picture draws the record's own geometry - the
breakout dot on the line at the breakout bar, a measure as long as the target distance,
at most five tags with none on another, every line inside the price panel."""
from __future__ import annotations

import json
import re
import xml.dom.minidom

import numpy as np
import pytest

import tascreen.chart_svg as cs
from synth import from_knots
from tascreen import alerts
from tascreen.patterns.chart import detect_chart
from tascreen.patterns.rules import load_rules

RULES = load_rules()
DOUBLE_BOTTOM = [(0, 130), (80, 100), (95, 112), (110, 100.5), (125, 113.5), (130, 115)]
HS_FALLING_NECK = [(0, 55), (90, 100), (105, 92.5), (120, 110), (135, 91.5), (150, 100.5), (165, 86), (168, 85)]
ASC_TRIANGLE = [(0, 80), (70, 95), (80, 100), (90, 90), (100, 100), (110, 93), (120, 100), (130, 96),
                (140, 104), (143, 105)]
RISING_WEDGE = [(0, 60), (70, 80), (80, 90), (90, 82), (100, 93), (110, 87), (120, 95), (130, 91),
                (140, 86), (143, 85)]
FLAG = ([(0, 50), (60, 50), (66, 60), (68, 58.2), (70, 59.4), (72, 57.6), (74, 58.8), (76, 57.2), (78, 62),
         (79, 62.5)], dict(noise=0.02, wiggle=0.15))
HIGH_TIGHT = ([(0, 40), (80, 40), (110, 85), (115, 83), (120, 84.5), (125, 82.5), (128, 88), (129, 89)],
              dict(noise=0.02, wiggle=0.15))
CUP = [(0, 60), (70, 100), (85, 85), (100, 80), (115, 80), (130, 85), (145, 99.5), (150, 95), (160, 97),
       (163, 101), (165, 102)]


def _detect(knots, pattern, **kw):
    bars = from_knots(knots, **kw)
    found = [d for d in detect_chart(bars, "TEST:SYN", RULES) if d.pattern == pattern]
    assert found, pattern
    row = found[0].row()
    record = {k: v for k, v in row.items() if not k.endswith("_json")}
    record["points"], record["lines"] = json.loads(row["points_json"]), json.loads(row["lines_json"])
    return bars, record


def _draw(monkeypatch, bars, record, live=None):
    """The SVG and the frame it was drawn in."""
    seen = {}
    real = cs._Frame

    class Frame(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seen["fr"] = self

    monkeypatch.setattr(cs, "_Frame", Frame)
    svg = alerts.pattern_chart(bars, record, live_price=live)
    xml.dom.minidom.parseString(svg)
    return svg, seen["fr"]


def _tags(svg):
    return [(float(m[1]), float(m[2]), float(m[3]), m[4]) for m in re.finditer(
        r'<g class="ann"><rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="22".*?<text[^>]*>(.*?)</text>', svg)]


def _index(bars):
    return {d: i for i, d in enumerate(bars["timestamp"].dt.strftime("%Y-%m-%d"))}


def _common(svg, fr):
    """The picture-wide rules: <= 5 tags, none on another, every line inside the panel."""
    tags = _tags(svg)
    assert len(tags) <= cs.TAG_BUDGET
    for k, a in enumerate(tags):
        for b in tags[k + 1:]:
            assert not (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + 22 and b[1] < a[1] + 22)
    for m in re.finditer(r'<line class="ann" x1="([\d.-]+)" y1="([\d.-]+)" x2="([\d.-]+)" y2="([\d.-]+)"', svg):
        assert fr.y0 - 0.5 <= float(m[2]) <= fr.y1 + 0.5 and fr.y0 - 0.5 <= float(m[4]) <= fr.y1 + 0.5
    assert "<polyline" not in svg                            # no moving averages by default
    assert 'stroke-dasharray="4 3"' not in svg               # no dashed box around the volume


def _breakout_dot(svg):
    m = re.search(r'<circle class="ann" cx="([\d.]+)" cy="([\d.]+)" r="7"', svg)
    return float(m[1]), float(m[2])


def _verticals(svg, color=cs.ANN["target"]):
    """The measure's vertical segments (x, length in px)."""
    return [(float(m[1]), abs(float(m[4]) - float(m[2]))) for m in re.finditer(
        rf'<line class="ann" x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)" stroke="{color}"', svg)
        if m[1] == m[3] and abs(float(m[4]) - float(m[2])) > 1]


# ------------------------------------------------------------------- helpers
def test_the_window_gives_the_pattern_half_and_never_cuts_its_first_bar():
    first, last = cs._window(500, 400, 495)
    assert (495 - 400 + 1) / (last - first + 1) >= 0.5 and first == 400 - cs.LEAD
    first, last = cs._window(500, 400, 440)                 # long after its breakout: a short lead-in
    assert first == 400 - cs.MIN_LEAD
    first, last = cs._window(600, 250, 590)                 # a long cup: more than MAX_BARS
    assert first <= 250 - cs.MIN_LEAD and last == 599
    first, last = cs._window(500, 480, 490)                 # a short flag still gets MIN_BARS
    assert last - first + 1 == cs.MIN_BARS
    assert cs._window(30, 2, 20)[0] == 0


def test_a_line_starts_at_its_first_turning_point_never_before_the_record():
    top = cs._Seg({"y1": 110.0, "y2": 100.0, "label": "קו עליון"}, 0, 50)
    bottom = cs._Seg({"y1": 80.0, "y2": 95.0, "label": "קו תחתון"}, 0, 50)
    points = [(0, 80.0, ""), (10, 108.0, ""), (20, 86.0, ""), (30, 104.0, "")]
    assert cs._first_touch(top, [top, bottom], points) == 10
    assert cs._first_touch(bottom, [top, bottom], points) == 0
    late = cs._Seg({"y1": 108.0, "y2": 100.0, "label": ""}, 12, 50)
    assert cs._first_touch(late, [late, bottom], points) == 30   # a touch before x1 does not count


def test_clip_keeps_only_the_part_inside_the_panel():
    box = (0.0, 0.0, 100.0, 100.0)
    assert cs._clip(10, -50, 10, 50, box) == (10, 0, 10, 50)
    x1, y1, x2, y2 = cs._clip(0, 150, 100, 50, box)
    assert (x1, y1) == (50, 100) and (x2, y2) == (100, 50)
    assert cs._clip(10, 120, 90, 130, box) is None


def test_the_cup_arc_passes_through_both_rims_and_the_bottom():
    bars, record = _detect(CUP, "cup_with_handle")
    index = _index(bars)
    anchors = [(index[p["date"]], p["price"]) for p in record["points"]]
    arc = cs._cup_arc(bars["low"].to_numpy(float), anchors)
    prices = dict(arc)
    for i, price in anchors:
        assert prices[i] == pytest.approx(price)
    assert min(prices.values()) == pytest.approx(anchors[1][1])
    assert max(prices.values()) <= max(anchors[0][1], anchors[2][1]) + 1e-9
    assert [i for i, _ in arc] == list(range(anchors[0][0], anchors[2][0] + 1))


def test_the_failing_bar_is_the_first_close_back_beyond_the_level():
    close = np.array([10, 12, 13, 11, 9.5, 9.0, 12.0])
    assert cs._failure_bar(close, 1, 10.0, True) == 4
    assert cs._failure_bar(close, 1, 8.0, True) is None
    assert cs._failure_bar(close, 0, 12.5, False) == 2


@pytest.mark.parametrize("knots, kw, pattern", [
    (DOUBLE_BOTTOM, {}, "double_bottom"), (HS_FALLING_NECK, {}, "head_shoulders_top"),
    (ASC_TRIANGLE, {}, "ascending_triangle"), (FLAG[0], FLAG[1], "flag"), (CUP, {}, "cup_with_handle")])
def test_the_measure_inside_the_pattern_is_as_long_as_the_target_distance(knots, kw, pattern):
    bars, record = _detect(knots, pattern, **kw)
    index = _index(bars)
    points = [(index[p["date"]], p["price"], p["label"]) for p in record["points"]]
    segs = cs._segments(record["lines"], index)
    day = lambda key: index[cs._iso(record[key])]               # noqa: E731
    length = record["target"] - record["breakout_price"]
    i, a, b = cs._measure(pattern, record["direction"] == "bullish", points, segs,
                          bars["high"].to_numpy(float), bars["low"].to_numpy(float),
                          day("start"), day("end"), day("breakout_date"), length)
    assert b - a == pytest.approx(length)
    if pattern == "double_bottom":                              # lower bottom up to the line
        assert a == min(p[1] for p in points[0::2]) and b == pytest.approx(points[1][1], abs=1e-3)
    if pattern == "head_shoulders_top":                         # head down to the neckline
        neck = segs[0]
        assert a == points[2][1] and b == pytest.approx(neck.value(points[2][0]), abs=1e-3)
    if pattern == "flag":                                       # the pole, end to end
        assert {round(a, 6), round(b, 6)} == {round(points[0][1], 6), round(points[1][1], 6)}
    if pattern == "cup_with_handle":                            # bottom up to the lower rim
        assert a == points[1][1] and b == pytest.approx(min(points[0][1], points[2][1]))


def test_the_trigger_is_the_confirmation_line_else_the_broken_side():
    neck = cs._Seg({"y1": 1.0, "y2": 2.0, "label": "קו צוואר"}, 0, 5)
    armpit = cs._Seg({"y1": 1.5, "y2": 1.5, "label": "קו אישור"}, 3, 5)
    assert cs._trigger([neck, armpit], False) is armpit
    assert cs._trigger([neck], False) is neck
    top = cs._Seg({"y1": 2.0, "y2": 2.0, "label": "קו עליון"}, 0, 5)
    bottom = cs._Seg({"y1": 1.0, "y2": 1.2, "label": "קו תחתון"}, 0, 5)
    assert cs._trigger([top, bottom], True) is top and cs._trigger([top, bottom], False) is bottom
    assert cs._trigger([top, bottom], None) is None


def test_axis_tags_that_meet_are_pushed_apart():
    fr = cs._Frame(0, 50, 90.0, 110.0)
    axis = cs._Axis(fr)
    axis.add(100.0, "#fff")
    axis.add(100.1, "#000", "חי")
    rows = axis.layout()
    assert abs(rows[0][2] - rows[1][2]) >= cs._Axis.GAP - 1e-9
    assert rows[1][4] == "" and rows[0][4] == "חי"             # the higher price on top


# ------------------------------------------------------------------ pictures
def test_a_double_bottom_breakout_marks_the_bar_on_the_line_and_measures_the_target(monkeypatch):
    bars, record = _detect(DOUBLE_BOTTOM, "double_bottom")
    svg, fr = _draw(monkeypatch, bars, record)
    _common(svg, fr)
    b = _index(bars)[cs._iso(record["breakout_date"])]
    x, y = _breakout_dot(svg)
    assert x == pytest.approx(fr.x(b), abs=0.1) and y == pytest.approx(fr.y(record["breakout_price"]), abs=0.1)
    words = [t[3] for t in _tags(svg)]
    assert f"פריצה {bars['timestamp'].iloc[b]:%d/%m}" in words and "יעד" in words
    assert f"{record['target']:,.2f}" in svg and "כלל המדידה" not in svg
    lengths = [n for _, n in _verticals(svg)]                  # the measure twice, same length
    span = fr.y(record["breakout_price"]) - fr.y(record["target"])
    assert sum(abs(n - span) < 0.5 for n in lengths) >= 2


def test_a_busted_pattern_shows_where_it_failed_and_no_target(monkeypatch):
    bars, record = _detect(DOUBLE_BOTTOM + [(133, 105), (137, 99.0)], "double_bottom")
    assert record["status"] == "busted"
    svg, fr = _draw(monkeypatch, bars, record)
    _common(svg, fr)
    words = [t[3] for t in _tags(svg)]
    assert "כשל" in words and "יעד" not in words and f"{record['target']:,.2f}" not in svg
    assert not _verticals(svg)


def test_a_falling_neckline_draws_the_armpit_line_as_the_trigger(monkeypatch):
    bars, record = _detect(HS_FALLING_NECK, "head_shoulders_top")
    svg, fr = _draw(monkeypatch, bars, record)
    _common(svg, fr)
    bold = re.findall(r'<line class="ann" x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)" '
                      r'stroke="[^"]+" stroke-width="2.8"', svg)
    assert len(bold) == 1 and bold[0][1] == bold[0][3]        # flat, at the armpit
    assert float(bold[0][1]) == pytest.approx(_breakout_dot(svg)[1], abs=0.2)
    assert sorted(t[3] for t in _tags(svg) if t[3] in ("ראש", "כתף")) == ["כתף", "כתף", "ראש"]


def test_a_wedge_aims_at_its_start_and_its_lines_stay_on_the_chart(monkeypatch):
    bars, record = _detect(RISING_WEDGE, "rising_wedge")
    svg, fr = _draw(monkeypatch, bars, record)
    _common(svg, fr)
    words = [t[3] for t in _tags(svg)]
    assert "תחילת הטריז" in words and "יעד" not in words and not _verticals(svg)


def test_a_high_tight_flag_has_a_pole_and_the_poles_height_as_target(monkeypatch):
    bars, record = _detect(HIGH_TIGHT[0], "high_tight_flag", **HIGH_TIGHT[1])
    svg, fr = _draw(monkeypatch, bars, record)
    _common(svg, fr)
    assert 'stroke-width="4.5"' in svg and "יעד" in svg


def test_a_flag_fills_at_least_half_the_window(monkeypatch):
    bars, record = _detect(FLAG[0], "flag", **FLAG[1])
    svg, fr = _draw(monkeypatch, bars, record)
    _common(svg, fr)
    index = _index(bars)
    span = index[cs._iso(record["breakout_date"])] - index[cs._iso(record["start"])] + 1
    assert span / fr.count >= 0.5 and 'stroke-width="4.5"' in svg and "תורן" in svg


def test_a_live_crossing_extends_the_real_line_to_the_live_session(monkeypatch):
    bars, record = _detect(ASC_TRIANGLE[:8] + [(135, 98.5)], "ascending_triangle")
    live = record["trigger_up"] * 1.006
    svg, fr = _draw(monkeypatch, bars, record, live=live)
    _common(svg, fr)
    assert f"חי {live:,.2f}" in svg and "פריצה מעל" in svg and "יעד" not in svg
    last = len(bars) - 1
    dashed = re.findall(r'<line class="ann" x1="[\d.]+" y1="[\d.]+" x2="([\d.]+)" y2="([\d.]+)" '
                        r'stroke="[^"]+" stroke-width="2.8"[^>]*stroke-dasharray', svg)
    assert len(dashed) == 1
    assert float(dashed[0][0]) == pytest.approx(fr.x(last + 1), abs=0.2)
    assert float(dashed[0][1]) == pytest.approx(fr.y(record["trigger_up"]), abs=0.3)
