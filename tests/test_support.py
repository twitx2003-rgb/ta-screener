"""Support tests (analyst/support.py) and how the analysis tells them: the walk over a level,
the 20/150-day averages as support, a breakout's retest, the support line, the key event and
the chart. Made-up prices and volumes only."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from synth import frame
from tascreen.analyst import load_rules
from tascreen.analyst import support
from tascreen.analyst.view import key_event, support_lines
from tascreen.analyst.writer import lead_problem, support_part

RULES = load_rules()
LEVEL, ATR = 100.0, 2.0          # touch <= 101, held >= 102, broke < 99, away: close >= 103 and low > 101


def _bars(rows, volumes=None):
    """(low, close) per session; open = the previous close, high just above the close."""
    out, prev = [], rows[0][1]
    for low, close in rows:
        out.append((prev, max(prev, close) + 0.2, low, close))
        prev = close
    return frame(out, volume=volumes)


def _walk(rows, volumes=None, start=0):
    bars = _bars(rows, volumes)
    n = len(bars)
    return support.walk(bars, np.full(n, LEVEL), np.full(n, ATR), start, RULES), bars


AWAY = [(108.0, 110.0)] * 5


def test_a_return_to_the_level_that_bounces_holds_with_its_volume():
    rows = AWAY + [(100.5, 101.5), (101.0, 103.0)] + [(105.0, 106.0)] * 3
    volumes = [1_000_000.0] * 6 + [2_000_000.0] + [1_000_000.0] * 3
    tests, _ = _walk(rows, volumes)
    assert len(tests) == 1
    t = tests[0]
    assert (t.touch, t.low, t.end, t.result) == (5, 5, 6, support.HELD)
    assert t.volume_at == 6 and round(t.volume_ratio, 2) == 2.0
    assert support.volume_ok(t.volume_ratio, RULES)


def test_a_close_through_the_level_breaks_it_and_a_new_test_needs_the_price_to_leave_again():
    rows = AWAY + [(99.5, 98.5)] + [(96.0, 97.0)] * 3 + [(100.6, 101.0)] + [(104.0, 106.0)] * 2 + [(100.8, 101.2)]
    tests, _ = _walk(rows)
    assert [(t.result, t.touch) for t in tests] == [(support.BROKE, 5), (support.TESTING, 12)]
    # the session at 101 right after the break is not a test: the price had not left yet
    assert all(t.touch != 9 for t in tests)


def test_hovering_just_above_the_level_is_not_a_string_of_tests():
    # after a hold, sessions that touch and close a little higher are the same test, not new ones
    rows = AWAY + [(100.5, 102.5)] + [(100.9, 102.4), (100.8, 102.6)] * 4
    tests, _ = _walk(rows)
    assert [t.result for t in tests] == [support.HELD]


def test_a_touch_that_hugs_the_level_too_long_is_dropped():
    rows = AWAY + [(100.5, 100.8)] * (RULES["support_max_test_sessions"] + 2)
    tests, _ = _walk(rows)
    assert tests == []


def test_weak_volume_is_under_the_threshold_the_analysis_calls_low():
    assert not support.volume_ok(0.8, RULES) and support.volume_ok(0.9, RULES)
    assert not support.volume_ok(math.nan, RULES)


# ------------------------------------------------------------------ the averages
def _uptrend_with_pullbacks(n_pullbacks: int, bounce_volume: float) -> pd.DataFrame:
    """A slow rise with pullbacks to about the 20-day average, each bouncing on `bounce_volume`."""
    closes, volumes = [], []
    price = 100.0
    for _ in range(40):                                   # a steady rise first
        price += 0.5
        closes.append(price)
        volumes.append(1_000_000.0)
    for _ in range(n_pullbacks):
        for _ in range(8):                                # run up
            price += 1.2
            closes.append(price)
            volumes.append(1_000_000.0)
        for _ in range(4):                                # pull back to the average
            price -= 1.6
            closes.append(price)
            volumes.append(700_000.0)
        for _ in range(3):                                # bounce
            price += 1.5
            closes.append(price)
            volumes.append(bounce_volume)
    rows, prev = [], closes[0]
    for c in closes:
        rows.append((prev, max(prev, c) + 0.3, min(prev, c) - 0.3, c))
        prev = c
    return frame(rows, volume=volumes)


class _F:
    def __init__(self):
        self.out = {}

    def add(self, key, value, label, unit="", digits=2):
        self.out[key] = value


def _ma_facts(bars, n=20):
    from tascreen.indicators import atr as atr_series

    f = _F()
    atr = atr_series(bars, 14).to_numpy()
    support.ma_facts(f, bars, n, atr, RULES, support.volume_ratios(bars["volume"]),
                     lambda i: bars["timestamp"].iloc[i].date().isoformat())
    return f.out


def test_repeated_holds_on_volume_that_is_not_weak_make_strong_support():
    facts = _ma_facts(_uptrend_with_pullbacks(3, 1_600_000.0))
    assert facts["sma20.support.held"] >= 2 and facts["sma20.support.streak"] >= 2
    assert facts["sma20.support.strength"].startswith("תמיכה חזקה")
    assert facts["sma20.support.last_volume"].startswith("גבוה")


def test_holds_on_weak_volume_are_not_called_strong():
    facts = _ma_facts(_uptrend_with_pullbacks(3, 500_000.0))
    assert facts["sma20.support.held"] >= 2
    assert facts["sma20.support.strength"] == "ממוצע 20 יום החזיק כתמיכה, אבל בנפח חלש"


# ------------------------------------------------------------------ a breakout's retest
def test_a_breakout_retest_that_held_is_told_with_its_bounce_volume():
    rows = [(96.0, 97.0)] * 5 + [(99.0, 101.0)] + [(104.0, 106.0)] * 3 + [(100.6, 101.5), (101.2, 104.0)] \
        + [(105.0, 107.0)] * 2
    volumes = [1_000_000.0] * 10 + [1_800_000.0] + [1_000_000.0] * 2
    bars = _bars(rows, volumes)
    f = _F()
    n = len(bars)
    tests = support.retest_facts(f, "pat_1", "קו הפריצה", np.full(n, LEVEL), 5, bars, np.full(n, ATR), RULES,
                                 support.volume_ratios(bars["volume"]), lambda i: f"2026-03-{i + 1:02d}")
    assert [t.result for t in tests] == [support.HELD]
    assert f.out["pat_1.retest"] == "המחיר חזר לבדוק את קו הפריצה ב-10/03, החזיק מעליו וקפץ בנפח גבוה, פי 1.8 מהממוצע"
    assert f.out["pat_1.retest_sessions_ago"] == 2


def test_a_breakout_that_has_not_left_its_line_is_not_told_yet_and_one_that_fell_back_is():
    beside = [(96.0, 97.0)] * 5 + [(99.0, 101.0), (100.2, 101.5)]
    bars = _bars(beside)
    f = _F()
    n = len(bars)
    assert support.retest_facts(f, "zone_1", "האזור שנפרץ", np.full(n, LEVEL), 5, bars, np.full(n, ATR), RULES,
                                support.volume_ratios(bars["volume"]), str) == []
    assert f.out == {}
    fell = beside + [(97.0, 97.5)]
    bars = _bars(fell)
    n = len(bars)
    support.retest_facts(f, "zone_1", "האזור שנפרץ", np.full(n, LEVEL), 5, bars, np.full(n, ATR), RULES,
                         support.volume_ratios(bars["volume"]), str)
    assert f.out["zone_1.retest"] == "המחיר נסגר שוב מתחת לאזור שנפרץ: הפריצה לא החזיקה"


# ------------------------------------------------------------------ how the analysis tells it
def _facts(**values):
    return {k: {"value": v} for k, v in values.items()}


def test_the_support_line_puts_a_retest_first_then_the_150_and_the_20_day_averages():
    facts = _facts(**{
        "pat_1.retest": "המחיר חזר לבדוק את קו הפריצה ב-10/03, החזיק מעליו וקפץ בנפח גבוה, פי 1.8 מהממוצע",
        "pat_1.name": "דגל", "pat_1.sessions_since_breakout": 6, "pat_1.state": "המחיר מעל קו הפריצה",
        "vs_sma150_pct": 12.0,
        "sma150.support.strength": "תמיכה חזקה: ממוצע 150 יום החזיק 3 פעמים ברצף, ובקפיצה האחרונה הנפח לא היה חלש",
        "sma150.support.last_low_day": "2026-01-20",
        "sma20.support.state": "המחיר בודק עכשיו את ממוצע 20 יום כתמיכה (נגע בו היום)", "vs_sma20_pct": 0.4})
    lines = support_lines(facts, RULES)
    assert [line["ma"] for line in lines] == [None, 150, 20]
    assert [line["tone"] for line in lines] == ["held", "held", "testing"]
    assert lines[1]["text"].endswith("(הבדיקה האחרונה ב-20/01)")
    part = support_part(facts, RULES)[0]
    assert part["part"] == "support" and part["signal"] == "green"
    assert part["text"].startswith("המחיר חזר לבדוק את קו הפריצה ב-10/03") and part["text"].endswith("(נגע בו היום).")


def test_under_the_150_day_average_the_line_says_so_in_red():
    facts = _facts(vs_sma150_pct=-6.5, vs_sma20_pct=-2.0)
    part = support_part(facts, RULES)[0]
    assert part["text"] == "המחיר 6.5% מתחת לממוצע 150 יום: הממוצע לא משמש עכשיו תמיכה."
    assert part["signal"] == "red"
    assert support_part(_facts(vs_sma150_pct=3.0), RULES) == []      # nothing to tell


def test_a_pattern_the_price_fell_back_into_is_left_to_the_key_event():
    facts = _facts(**{"pat_1.retest": "המחיר נסגר שוב מתחת לקו הפריצה: הפריצה לא החזיקה", "pat_1.name": "דגל",
                      "pat_1.sessions_since_breakout": 4, "pat_1.state": "המחיר חזר אל תוך התבנית: הפריצה מוטלת בספק"})
    assert support_lines(facts, RULES) == []


def test_the_key_event_tells_a_held_retest_and_a_test_of_the_150_day_average():
    rules = {**RULES, "event_fresh_sessions": 10}
    held = key_event(_facts(**{
        "pat_1.state": "המחיר מעל קו הפריצה", "pat_1.name": "דגל", "pat_1.direction": "שורי",
        "pat_1.sessions_since_breakout": 12, "pat_1.breakout_day": "2026-03-02",
        "pat_1.retest": "המחיר חזר לבדוק את קו הפריצה ב-10/03, החזיק מעליו וקפץ בנפח רגיל, סביב הממוצע",
        "pat_1.retest_sessions_ago": 3}), "", rules)
    assert held == ("אחרי הפריצה מתבנית דגל ב-02/03, המחיר חזר לבדוק את קו הפריצה ב-10/03, החזיק מעליו "
                    "וקפץ בנפח רגיל, סביב הממוצע", "retest_held")
    testing = key_event(_facts(**{
        "sma150.support.state": "המחיר בודק עכשיו את ממוצע 150 יום כתמיכה (ירד אליו ב-18/03)",
        "sma150.support.sessions_ago": 0,
        "sma150.support.strength": "תמיכה חזקה: ממוצע 150 יום החזיק 2 פעמים ברצף, ובקפיצה האחרונה הנפח לא היה חלש"}),
        "", rules)
    assert testing[1] == "ma_support_test" and testing[0].startswith("המחיר בודק עכשיו את ממוצע 150 יום")
    assert "(תמיכה חזקה:" in testing[0]
    # the headline's lead may say the support held
    facts = _facts(event="ממוצע 150 יום החזיק כתמיכה: המחיר ירד אליו ב-18/03 וקפץ ממנו", **{"event.kind": "ma_support_held"})
    assert lead_problem("תמיכה שהחזיקה: ממוצע 150 יום החזיק כתמיכה.", facts) is None


def test_the_chart_draws_the_20_day_average_only_when_the_support_line_names_it(monkeypatch):
    from tascreen.analyst import view

    named = _facts(**{"sma20.support.state": "המחיר בודק עכשיו את ממוצע 20 יום כתמיכה (נגע בו היום)"})
    assert any(line["ma"] == 20 for line in support_lines(named, RULES))
    assert not any(line["ma"] == 20 for line in support_lines(_facts(vs_sma20_pct=-1.0), RULES))
    assert view.NOTE_WORDS["sma20"] == "ממוצע 20 יום"


def test_the_whole_analysis_carries_the_support_facts():
    from tascreen.analyst.facts import analyse

    bars = _uptrend_with_pullbacks(8, 1_600_000.0)                 # 160 sessions: the 150-day exists
    facts = analyse(bars, "TEST:SYN").facts
    assert "sma20" in facts and "sma20.support.held" in facts and "sma150" in facts
    assert facts["sma20.support.strength"]["value"].startswith("תמיכה חזקה")
