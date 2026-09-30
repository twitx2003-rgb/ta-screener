"""The breakout research team: candidates, shortlist, dossiers, the team's checked picks,
the evening report and its fallback, and the weekly learning. Synthetic bars only (the
scan_setup store: a forming double bottom and a head and shoulders top)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from scan_setup import RULES, _setup
from tascreen import alerts, research
from tascreen.llm import SyntheticLLM
from tascreen.web.data import ScanRepository


def _view(tmp_path):
    settings, store, day = _setup(tmp_path)
    view = ScanRepository(store, RULES).current()
    verge = [{**v, "hit_rate": 55.0} for v in alerts.on_the_verge(view, 10.0)]
    assert verge, "the synthetic double bottom should be near its line"
    return settings, store, view, verge


def team(chief_picks, verdict_score=8):
    """A synthetic team: every specialist scores every dossier; the chief answers with
    `chief_picks(dossiers)`."""
    def answer(system, user, schema):
        data = json.loads(user)
        if "verdicts" in schema["properties"]:
            return {"verdicts": [{"symbol": d["symbol"], "score": verdict_score, "for": ["clean"],
                                  "against": []} for d in data["dossiers"]]}
        return {"picks": chief_picks(data["dossiers"])}
    return SyntheticLLM(answer)


def good_pick(dossiers):
    d = dossiers[0]
    line = d["pattern_detail"]["line"]
    return [{"symbol": d["symbol"], "conviction": 8, "why_he": f"סגירה קרובה לקו {line} ומגמה עולה.",
             "cancels_he": "אם הקו לא נפרץ.", "watch_he": f"סגירה מעל {line}."}]


def test_candidates_shortlist_and_dossier(tmp_path):
    settings, store, view, verge = _view(tmp_path)
    cands = research.candidates(view, [], verge)
    assert cands and all(c["kind"] == "verge" and c["record"] for c in cands)
    stocks = {r["symbol"]: r for r in view.stocks.to_dict("records")}
    short = research.shortlist(cands, stocks, view.day, 10.0, 8)
    assert short[0]["score"] >= short[-1]["score"]
    d = research.dossier(short[0], view, stocks, store.read_ledger(), store.read_backtest(),
                         store.read_bars(short[0]["symbol"]), {"SPY": 0.4}, None)
    assert d["kind"] == "verge" and d["pattern_detail"]["line"] and d["market"] == {"SPY": 0.4}
    assert "history" in d and "rs_20d_pctile" in d


def test_the_team_picks_and_a_made_up_number_drops_a_pick(tmp_path):
    settings, store, view, verge = _view(tmp_path)

    def picks(dossiers):
        bad = {**good_pick(dossiers)[0], "why_he": "יעד ב-987.65 בטוח."}
        return good_pick(dossiers) + [{**bad, "symbol": dossiers[0]["symbol"]}]

    llm = team(picks)
    result = research.research(view, store, [], verge, llm=llm, verge_pct=10.0, shortlist_size=8, max_picks=3)
    assert result["status"] == "picked" and len(result["picks"]) == 1
    assert len(llm.calls) == 4                                    # three specialists and the chief
    assert result["picks"][0]["scores"] == {"pattern-auditor": 8, "context-analyst": 8, "statistician": 8}
    only_bad = team(lambda ds: [{**good_pick(ds)[0], "watch_he": "כדאי לקנות עכשיו."}])
    failed = research.research(view, store, [], verge, llm=only_bad, verge_pct=10.0, shortlist_size=8, max_picks=3)
    assert failed["status"] == "failed" and failed["dropped"] == ["advice or forecast wording"]
    none = research.research(view, store, [], verge, llm=team(lambda ds: []), verge_pct=10.0, shortlist_size=8,
                             max_picks=3)
    assert none["status"] == "none" and none["picks"] == []


def test_variety_in_the_shortlist_and_the_picks_and_only_proven_patterns(tmp_path):
    cands = [{"symbol": f"NYSE:W{i}", "pattern": "rising_wedge", "kind": "verge", "gap_pct": 0.5,
              "hit_rate": 60.0} for i in range(4)]
    cands.append({"symbol": "NYSE:DB", "pattern": "double_bottom", "kind": "verge", "gap_pct": 1.5,
                  "hit_rate": 50.0})
    short = research.shortlist(cands, {}, date(2026, 1, 5), 2.0, 8, per_pattern=2)
    assert [c["symbol"] for c in short] == ["NYSE:W0", "NYSE:W1", "NYSE:DB"]
    assert len(research.shortlist(cands, {}, date(2026, 1, 5), 2.0, 8)) == 5          # four of one pattern
    dossiers = [{"symbol": s, "pattern": p} for s, p in
                (("NYSE:A", "double_bottom"), ("NYSE:B", "double_bottom"), ("NYSE:D", "double_bottom"),
                 ("NYSE:C", "rectangle"))]
    plain = {"conviction": 7, "why_he": "מגמה עולה ונפח גבוה.", "cancels_he": "אם הקו לא נפרץ.",
             "watch_he": "סגירה מעל הקו."}
    out = research.run_team(dossiers, team(lambda ds: [{"symbol": d["symbol"], **plain} for d in ds]),
                            max_picks=10)
    assert [p["symbol"] for p in out["picks"]] == ["NYSE:A", "NYSE:B", "NYSE:C"]   # two picks per pattern
    settings, store, view, verge = _view(tmp_path)
    result = research.research(view, store, [], verge, llm=team(good_pick), verge_pct=10.0, shortlist_size=8,
                               max_picks=3, patterns={"rectangle"})
    assert result["status"] == "none" and result["unproven"] == len(verge) and result["candidates"] == 0


class Bot:
    def __init__(self):
        self.sent, self.albums = [], []

    def send(self, text, html=False):
        self.sent.append(text)

    def send_album(self, photos):
        self.albums.append(photos)


def test_the_evening_report_sends_only_the_picks_and_records_them(tmp_path):
    settings, store, view, verge = _view(tmp_path)
    cfg = settings.alerts.__class__(verge_pct=10.0, watch_pct=10.0)       # the synthetic verge is 7% away
    run = lambda b, v: research.research(view, store, b, v, llm=team(good_pick), verge_pct=10.0,  # noqa: E731
                                         shortlist_size=8, max_picks=3)
    bot, started = Bot(), []
    out = research.evening_report(store, view, cfg, bot=bot, min_cases=1, run=run,
                                  dispatch=lambda wf, inputs: started.append(inputs["symbol"]) or 204,
                                  can_dispatch=True, fallback=lambda note: pytest.fail("no fallback"),
                                  to_png=lambda svg: b"png")
    assert out["status"] == "sent" and out["research"] == "picked" and out["picks"] == 1
    assert bot.sent[0].startswith("<b>🏆 המובחרות של") and "ובחר אחת." in bot.sent[0]
    assert len(bot.albums) == 1 and bot.albums[0][0][1].startswith("🏆 <b>1.</b>")
    assert started and research.read_picks(store)[0]["kind"] == "verge"
    again = research.evening_report(store, view, cfg, bot=bot, min_cases=1, run=run, dispatch=lambda *a: 204,
                                     can_dispatch=True, fallback=lambda note: pytest.fail("no fallback"))
    assert again == {"status": "already sent"}


def test_a_failed_research_falls_back_to_the_regular_report(tmp_path):
    settings, store, view, verge = _view(tmp_path)

    def broken(b, v):
        raise RuntimeError("claude down")

    notes = []
    out = research.evening_report(store, view, settings.alerts, bot=Bot(), min_cases=1, run=broken,
                                  dispatch=lambda *a: 204, can_dispatch=False,
                                  fallback=lambda note: notes.append(note) or {"status": "sent"})
    assert out == {"status": "sent", "research": "RuntimeError"} and notes == [research.RESEARCH_FAILED_NOTE]


def test_text_rules():
    assert research.text_problem("סגירה מעל 45.2 וממוצע 50.", [45.2]) is None
    assert research.text_problem("יעד 60.", [45.2]) == "number 60 not in the sources"
    assert research.text_problem("כדאי לקנות.", []) == "advice or forecast wording"


def test_the_weekly_review_learns_from_outcomes(tmp_path):
    settings, store, view, verge = _view(tmp_path)
    (research.research_dir(store)).mkdir(parents=True, exist_ok=True)
    picks = [{"day": "2026-01-05", "symbol": "NYSE:AAA", "pattern": "double_bottom", "kind": "breakout",
              "start": "2025-11-01", "conviction": 8, "scores": {}, "why_he": "x"},
             {"day": "2026-01-06", "symbol": "NYSE:BBB", "pattern": "double_bottom", "kind": "verge",
              "start": "2025-11-02", "conviction": 7, "scores": {}, "why_he": "y"}]
    (research.research_dir(store) / "picks.json").write_text(json.dumps(picks), encoding="utf-8")
    ledger = pd.DataFrame({"symbol": ["NYSE:AAA"], "pattern": ["double_bottom"], "start_day": ["2025-11-01"],
                           "outcome": ["target"]})
    marked = research.outcomes_of(picks, ledger)
    assert [p["outcome"] for p in marked] == ["target", "no breakout yet"]

    coach = SyntheticLLM(lambda s, u, sc: {"lessons": ["Prefer breakouts over verge picks [1 of 1]"],
                                           "summary_he": "הבחירות עדיין מעטות."})
    now = datetime(2026, 1, 10, 6, 0, tzinfo=timezone.utc)
    assert research.weekly_due(store, now)
    review = research.weekly_review(store, coach, now)
    assert review["lessons"] == 1 and research.read_lessons(store).startswith("- Prefer breakouts")
    assert not research.weekly_due(store, now)
    text = research.weekly_message(review)
    assert text.startswith("📚 <b>סיכום שבועי של צוות המחקר</b>") and "הבחירות עדיין מעטות." in text
