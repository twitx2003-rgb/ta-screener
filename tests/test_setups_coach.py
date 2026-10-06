"""The setups coach and its study: the ledger of the list's entries, the history study, the
coach's model call and checks, the owner's note, and the notes the chart analyst reads.
Made-up stocks, prices and volumes only."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from synth import frame
from tascreen import setups_coach, setups_study
from tascreen.analyst import load_rules
from tascreen.llm import SyntheticLLM

RULES = load_rules()


def _bars(closes):
    rows, prev = [], closes[0]
    for c in closes:
        rows.append((prev, max(prev, c) + 1.0, min(prev, c) - 1.0, c))
        prev = c
    return frame(rows)


BARS = _bars([100.0] * 30 + [101.0, 104.0, 106.0, 103.0, 99.0])
DAYS = [str(t.date()) for t in BARS["timestamp"]]


def _state(events, entries=()):
    return {"events": events, "entries": list(entries)}


# ------------------------------------------------------------------ the ledger
def test_an_entry_opens_a_record_and_its_exit_closes_it_with_its_moves():
    entry = {"symbol": "NYSE:AAA", "kind": "ma150", "volume": 1.6, "strict": True, "since": DAYS[29],
             "watch": 98.0, "text": "held"}
    opened = setups_coach.record([], _state([{"symbol": "NYSE:AAA", "kind": "ma150", "what": "added", "text": "held"}],
                                            [entry]), DAYS[30], price_of=lambda s: 101.0)
    assert opened[0]["open"] and opened[0]["entry"] == 101.0 and opened[0]["level"] == 98.0
    renewed = setups_coach.record(opened, _state([{"symbol": "NYSE:AAA", "kind": "retest", "what": "renewed"}]),
                                  DAYS[32], price_of=lambda s: 106.0)
    assert renewed[0]["renewed"] == 1 and renewed[0]["kind_now"] == "retest"
    closed = setups_coach.record(renewed, _state([{"symbol": "NYSE:AAA", "what": "removed", "reason": "fell_ma"}]),
                                 DAYS[34], price_of=lambda s: 99.0, bars_of=lambda s: BARS)
    rec = closed[0]
    assert not rec["open"] and rec["reason"] == "fell_ma" and rec["ret_pct"] == -1.98
    assert rec["sessions"] == 4 and rec["mfe_pct"] == round((107.0 / 101.0 - 1) * 100, 2)
    assert rec["mae_pct"] == round((98.0 / 101.0 - 1) * 100, 2)
    summary = setups_coach.record_summary(closed)
    assert summary["decided"] == 1 and summary["by_reason"]["fell_ma"] == {"n": 1, "up_pct": 0.0, "ret_avg": -1.98}


def test_an_exit_at_the_open_is_recorded_at_the_open_price_without_bars():
    opened = [{"symbol": "NYSE:AAA", "open": True, "added": DAYS[30], "entry": 100.0}]
    closed = setups_coach.record(opened, _state([{"symbol": "NYSE:AAA", "what": "removed", "reason": "opened_below"}]),
                                 DAYS[31], price_of={"NYSE:AAA": 96.5}.get)
    assert closed[0]["exit"] == 96.5 and closed[0]["ret_pct"] == -3.5 and "mfe_pct" not in closed[0]


# ------------------------------------------------------------------ the coach
def _answer(**over):
    base = {"why_fell": [{"symbol": "NYSE:AAA", "text": "weak bounce volume"}],
            "keep": ["Keep high-volume retests [study]"], "improve": ["ma20 volume 1.15 -> 1.5 [study]"],
            "analyst_notes": ["A retest that held on high volume is the strongest support test",
                              "Quote 45% of failures"],                    # a number: dropped
            "summary_he": "מניה אחת יצאה היום, AAA, אחרי ירידה של 1.98%.",
            "why_he": [{"symbol": "NYSE:AAA", "text": "הקפיצה הייתה בנפח חלש."}],
            "improve_he": ["להעלות את סף המחזור בממוצע 20 ל-1.5"]}
    return {**base, **over}


def _user(left=()):
    study = {"baseline": {"n": 10}, "ma20": {"volume": {"high >=1.5": {"n": 5, "failed_pct": 40.0}}}}
    return setups_coach.inputs(_state([]), list(left), study, {"min_bounce_volume": 1.15}, "", DAYS[34])


def test_the_coach_keeps_only_text_whose_numbers_are_in_its_input():
    left = [{"symbol": "NYSE:AAA", "open": False, "left": DAYS[34], "reason": "fell_ma", "ret_pct": -1.98}]
    got = setups_coach.coach(SyntheticLLM(lambda system, user, schema: _answer()), _user(left))
    assert got["summary_he"].startswith("מניה אחת יצאה") and got["why_he"][0]["symbol"] == "NYSE:AAA"
    assert got["analyst_notes"] == ["A retest that held on high volume is the strongest support test"]
    assert got["improve_he"] == ["להעלות את סף המחזור בממוצע 20 ל-1.5"]
    made_up = setups_coach.coach(SyntheticLLM(lambda s, u, sc: _answer(summary_he="שלוש מניות ירדו 7.3%.")), _user(left))
    assert made_up["summary_he"] == ""


def test_the_coach_saves_its_lessons_and_the_analysts_notes(tmp_path):
    result = setups_coach.coach(SyntheticLLM(lambda s, u, sc: _answer()), _user())
    setups_coach.save(tmp_path, DAYS[34], result)
    assert "Keep: Keep high-volume retests" in setups_coach.read_lessons(tmp_path)
    notes = setups_coach.analyst_notes(tmp_path)
    assert notes.startswith("NOTES FROM THE SETUPS COACH") and "strongest support test" in notes
    assert json.loads((tmp_path / "setups" / "coach" / f"{DAYS[34]}.json").read_text(encoding="utf-8"))["keep"]
    assert setups_coach.analyst_notes(tmp_path / "none") == ""


def test_the_owners_note_comes_every_day_and_the_proposals_on_fridays():
    left = [{"symbol": "NYSE:AAA", "reason": "fell_ma", "ret_pct": -1.98}]
    result = {**_answer(), "why_he": [{"symbol": "NYSE:AAA", "text": "הקפיצה הייתה בנפח חלש."}]}
    text = setups_coach.message(result, left)
    assert "סיכום יום" in text and "• AAA: נסגרה מתחת לממוצע (-2.0%). הקפיצה הייתה בנפח חלש." in text
    assert "הצעות" not in text
    weekly = setups_coach.message(result, [], weekly=True)
    assert "סיכום שבועי" in weekly and "להעלות את סף המחזור" in weekly
    # a quiet day still gets its note (owner, 2026-10-06), with the program's own line
    quiet = setups_coach.message({**result, "summary_he": ""}, [], on_list=12, record={"decided": 0})
    assert "סיכום יום" in quiet and quiet.endswith("ברשימה עכשיו 12 מניות; עוד אין מניות שיצאו מאז שהיומן נפתח.")
    decided = setups_coach.message(result, [], on_list=12, record={"decided": 4, "all": {"up_pct": 75.0}})
    assert decided.endswith("ברשימה עכשיו 12 מניות; הוכרעו עד היום 4, 75% מהן ברווח ביציאה.")


def test_the_analyst_writes_with_the_coachs_notes(tmp_path):
    from test_support import _uptrend_with_pullbacks
    from tascreen.analyst.facts import analyse
    from tascreen.analyst.writer import write

    seen = {}

    def answer(system, user, schema):
        seen["system"] = system
        return {"parts": []}

    write(analyse(_uptrend_with_pullbacks(8, 1_600_000.0), "TEST:SYN"), SyntheticLLM(answer),
          notes="NOTES FROM THE SETUPS COACH:\n- a note")
    assert seen["system"].endswith("NOTES FROM THE SETUPS COACH:\n- a note")


# ------------------------------------------------------------------ the study
def test_a_setup_is_measured_from_its_close_over_the_next_sessions():
    close = np.array([100.0, 101, 103, 98.5, 104, 105, 106, 107, 108, 109, 110, 111, 112])
    high, low = close + 1, close - 1
    level, atr = np.full(len(close), 100.0), np.full(len(close), 2.0)
    got = setups_study._measure(close, high, low, level, atr, 1, 0.5, horizon=10)
    assert got["failed"] and got["failed_after"] == 2                     # 98.5 < 100 - 1
    assert got["ret5_pct"] == round((106 / 101 - 1) * 100, 2) and got["ret20_pct"] is None
    assert got["mfe_pct"] == round((112 / 101 - 1) * 100, 2)
    assert setups_study._measure(close, high, low, level, atr, 5, 0.5, horizon=10) is None   # too near the end


def test_the_study_finds_the_holds_and_a_breakouts_retest_without_looking_ahead():
    from test_support import _uptrend_with_pullbacks

    bars = _uptrend_with_pullbacks(10, 1_600_000.0)                  # 190 sessions: a baseline too
    days = [str(t.date()) for t in bars["timestamp"]]
    breakout = {"pattern": "flag", "breakout_day": days[60], "breakout_price": float(bars["close"].iloc[59]),
                "lines": []}
    wedge = {**breakout, "pattern": "rising_wedge"}
    got = setups_study.study_stock("TEST:SYN", bars, [breakout, wedge], RULES)
    kinds = {s["kind"] for s in got["setups"]}
    assert "ma20" in kinds and "breakout" in kinds
    assert all(s.get("pattern") != "rising_wedge" for s in got["setups"])
    holds = [s for s in got["setups"] if s["kind"] == "ma20"]
    assert holds[0]["streak"] == 1 and holds[-1]["streak"] > 1 and holds[0]["volume"] > 1.15
    assert got["baseline"] and got["above"]
    table = setups_study.summarise(pd.DataFrame(got["setups"] + got["baseline"]).assign(liquid=True, breadth_pct=50.0))
    assert table["ma20"]["all"]["n"] == len(holds) and "volume" in table["ma20"] and "streak" in table["ma20"]
