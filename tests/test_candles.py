"""Candlestick detectors on hand-built candles (synthetic prices)."""
from __future__ import annotations

import pytest

from synth import candles
from tascreen.patterns.candles import DETECTORS, detect_candles
from tascreen.patterns.rules import load_rules

RULES = load_rules()


def found(bars) -> set[str]:
    return {d.pattern for d in detect_candles(bars, "TEST:SYN", RULES)}


HAMMER = [(100.0, 100.25, 98.9, 100.2)]
ENGULF_BULL = [(100.0, 100.2, 99.2, 99.4), (99.3, 100.4, 99.2, 100.3)]
MORNING_STAR = [(100.0, 100.1, 98.7, 98.8), (98.4, 98.6, 98.2, 98.5), (98.7, 99.7, 98.6, 99.6)]
SOLDIERS = [(100.0, 101.1, 99.9, 101.0), (100.5, 101.7, 100.4, 101.6), (101.2, 102.4, 101.1, 102.3)]
SHOOTING_STAR = [(100.0, 101.3, 99.97, 100.2)]
DOJI = [(100.0, 100.6, 99.5, 100.02)]
PIERCING = [(100.0, 100.1, 98.9, 99.0), (98.7, 99.9, 98.6, 99.8)]
DARK_CLOUD = [(100.0, 101.1, 99.9, 101.0), (101.3, 101.4, 100.2, 100.3)]
HARAMI_BULL = [(100.0, 100.1, 98.7, 98.8), (99.0, 99.6, 98.9, 99.5)]
MARUBOZU_WHITE = [(100.0, 101.0, 100.0, 101.0)]


@pytest.mark.parametrize("pattern,shape,trend", [
    ("hammer", HAMMER, "down"),
    ("hanging_man", HAMMER, "up"),
    ("bullish_engulfing", ENGULF_BULL, "down"),
    ("morning_star", MORNING_STAR, "down"),
    ("three_white_soldiers", SOLDIERS, "down"),
    ("shooting_star", SHOOTING_STAR, "up"),
    ("northern_doji", DOJI, "up"),
    ("southern_doji", DOJI, "down"),
    ("piercing", PIERCING, "down"),
    ("dark_cloud_cover", DARK_CLOUD, "up"),
    ("bullish_harami", HARAMI_BULL, "down"),
    ("white_marubozu", MARUBOZU_WHITE, "up"),
])
def test_textbook_candles_are_found(pattern, shape, trend):
    assert pattern in found(candles(30, shape, trend=trend))


@pytest.mark.parametrize("pattern,shape,trend", [
    ("hammer", HAMMER, "up"),                      # same shape, wrong trend: a hanging man
    ("bullish_engulfing", ENGULF_BULL, "up"),
    ("morning_star", MORNING_STAR, "up"),
    ("shooting_star", SHOOTING_STAR, "down"),
    ("northern_doji", DOJI, "down"),
])
def test_right_shape_in_the_wrong_trend_is_not_the_pattern(pattern, shape, trend):
    assert pattern not in found(candles(30, shape, trend=trend))


def test_engulfing_needs_the_bodies_engulfed():
    not_engulfing = [(100.0, 100.2, 99.2, 99.4), (99.5, 100.4, 99.4, 100.3)]   # opens above prior close
    assert "bullish_engulfing" not in found(candles(30, not_engulfing, trend="down"))


def test_every_rule_file_candle_has_a_detector_and_back():
    assert set(RULES.candle) == set(DETECTORS)


def test_detection_carries_its_checklist():
    det = next(d for d in detect_candles(candles(30, HAMMER, trend="down"), "TEST:SYN", RULES)
               if d.pattern == "hammer")
    rules = {c.rule for c in det.checks}
    assert {"prior_trend", "not_doji", "long_lower_shadow", "little_upper_shadow"} <= rules
    assert all(c.passed for c in det.checks)
    origins = {c.rule: c.origin for c in det.checks}
    assert origins["long_lower_shadow"] == "site" and origins["little_upper_shadow"] == "ours"
    assert det.status == "signal" and det.direction == "bullish"


def test_only_recent_candles_are_reported():
    bars = candles(30, HAMMER + [(100.2, 100.8, 100.1, 100.7)] * 4, trend="down")
    assert "hammer" not in found(bars)          # the hammer is 5 sessions back; recent = 3
