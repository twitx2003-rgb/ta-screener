"""The morning digest: its window (no Sunday, Monday from Friday), the stories in it, the
editor's checked items, the picture's page, and one morning's run. Synthetic stories and
a synthetic model only."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tascreen import digest, digest_render
from tascreen.llm import SyntheticLLM

NY = "America/New_York"
TUESDAY_10 = datetime(2026, 9, 29, 7, 0, tzinfo=timezone.utc)        # 10:00 Israel, 03:00 New York


def at(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc).timestamp()


def story(n: int, when: float, importance: int = 4, text: str = "") -> dict:
    return {"at": when, "id": str(n), "author": f"Desk{n}", "url": f"https://x.com/Desk{n}/status/{n}",
            "importance": importance, "summary_he": text or f"חברה {n} דיווחה על עלייה של 12.5% בהכנסות.",
            "analysis_he": "", "sources": [], "sent": False}


def test_the_window_starts_at_the_last_sessions_open():
    opens, day = digest.window(TUESDAY_10, NY)
    assert day.isoformat() == "2026-09-28" and opens == datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)
    monday = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)
    assert digest.window(monday, NY)[1].isoformat() == "2026-10-02"                    # from Friday
    assert digest.window(datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc), NY)[1].isoformat() == "2026-10-02"
    assert digest.window(datetime(2026, 10, 4, 7, 0, tzinfo=timezone.utc), NY) is None  # Sunday: none


def test_due_at_ten_once_a_morning_and_three_tries_at_most():
    assert digest.due(TUESDAY_10, {}, NY)
    assert not digest.due(datetime(2026, 9, 29, 6, 50, tzinfo=timezone.utc), {}, NY)     # 09:50
    assert not digest.due(TUESDAY_10, {"sent": "2026-09-29"}, NY)
    assert digest.due(TUESDAY_10, {"day": "2026-09-29", "tries": 2}, NY)
    assert not digest.due(TUESDAY_10, {"day": "2026-09-29", "tries": 3}, NY)


def test_the_stories_of_the_window_most_important_first():
    log = [story(1, at(28, 12)), story(2, at(28, 14), 3), story(3, at(28, 20), 5), story(3, at(28, 20), 5),
           story(4, at(29, 8))]                                    # before the open, twice, after 10:00
    opens, _ = digest.window(TUESDAY_10, NY)
    assert [r["id"] for r in digest.stories(log, opens, TUESDAY_10)] == ["3", "2"]


def item(ids, title="חברה 1 מדווחת", detail="עלייה של 12.5% בהכנסות.", category="company", tickers=("aaa", "$BB", "x y")):
    return {"ids": list(ids), "category": category, "title_he": title, "detail_he": detail, "tickers": list(tickers)}


def test_the_editors_items_are_checked_against_the_stories():
    picked = [story(n, at(28, 15)) for n in range(1, 4)]
    answer = {"headline_he": "יום של דוחות.", "watch_he": "",
              "items": [item(["1"]), item(["2"], detail="זינוק של 87.3% במניה."), item(["99"]),
                        item(["3"], title="כדאי לקנות עכשיו", category="nope")]}
    written, dropped = digest.check(answer, picked, {"SPY": -0.42})
    assert [i["ids"] for i in written["items"]] == [["1"]]
    assert written["items"][0]["tickers"] == ["AAA", "BB"]            # capitals, no $, two at most
    assert dropped == ["number 87.3 not in the sources", "no story of the window", "advice or forecast wording"]
    ok, _ = digest.check({"headline_he": "S&P 500 ירד 0.42%.", "watch_he": "", "items": []}, picked, {"SPY": -0.42})
    assert ok["headline_he"] == "S&P 500 ירד 0.42%."                    # the index moves are sources too


def test_a_digest_is_built_and_a_thin_answer_is_asked_again():
    log = [story(n, at(28, 15 + n)) for n in range(1, 7)]
    answers = [{"headline_he": "שוק שקט.", "watch_he": "", "items": [item(["1"])]},
               {"headline_he": "יום של דוחות חזקים.", "watch_he": "מחר: נתוני תעסוקה.",
                "items": [item([str(n)], title=f"חברה {n} מדווחת") for n in range(1, 7)]}]
    llm = SyntheticLLM(lambda s, u, sc: answers.pop(0))
    d = digest.build(TUESDAY_10, log, {"SPY": -0.42, "QQQ": 0.3}, llm, NY)
    assert len(llm.calls) == 2 and len(d["items"]) == 6 and d["headline_he"] == "יום של דוחות חזקים."
    assert d["window_he"] == "מפתיחת המסחר אתמול, 16:30, עד 10:00" and d["date_he"] == "שלישי · 29/09/2026"
    assert d["indexes"] == [{"name": "S&P 500", "change": -0.42}, {"name": "נאסד\"ק 100", "change": 0.3}]
    assert digest.build(TUESDAY_10, log[:2], {}, llm, NY) is None                   # too few stories


def test_the_page_has_the_height_it_says_and_escapes_the_text():
    d = {"date_he": "שלישי", "window_he": "w", "headline_he": "<b>x</b>", "watch_he": "",
         "indexes": [{"name": "S&P 500", "change": -0.42}],
         "items": [item(["1"], title="<i>t</i>") for _ in range(10)], "footer_he": "f"}
    page, width, height = digest_render.render_html(d)
    assert width == 1080 and height == digest_render.height(8, strip=True, watch=False)
    assert page.count('class="block item"') == 8 and "&lt;b&gt;x&lt;/b&gt;" in page and "<i>t</i>" not in page
    assert "-0.42%" in page and digest_render.FONT.exists()


def test_one_morning_sends_once_and_counts_its_tries(tmp_path):
    state, record = tmp_path / "state.json", tmp_path / "digest.json"
    state.write_text(json.dumps({"day_log": [story(n, at(28, 15 + n)) for n in range(1, 5)]}), encoding="utf-8")
    llm = SyntheticLLM(lambda s, u, sc: {"headline_he": "יום של דוחות.", "watch_he": "",
                                         "items": [item([str(n)]) for n in range(1, 5)]})
    photos = []

    def draw(d, folder):
        png = folder / "digest.png"
        folder.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"png")
        return png

    kw = dict(state_path=state, record_path=record, market_tz=NY, make_llm=lambda: llm,
              send_photo=lambda png, caption: photos.append(caption), moves_of=lambda day: {},
              draw=draw, folder=tmp_path / "out")
    assert digest.run(now=datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc), **kw) == {"status": "not due"}
    out = digest.run(now=TUESDAY_10, **kw)
    assert out["status"] == "sent" and out["items"] == 4 and len(photos) == 1
    assert photos[0].startswith("☀️ <b>סיכום הבוקר של וול סטריט</b>")
    assert json.loads(record.read_text(encoding="utf-8"))["sent"] == "2026-09-29"
    assert digest.run(now=TUESDAY_10, **kw) == {"status": "not due"} and len(photos) == 1
    broken = {**kw, "record_path": tmp_path / "other.json", "make_llm": lambda: 1 / 0}
    for _ in range(3):
        try:
            digest.run(now=TUESDAY_10, **broken)
        except ZeroDivisionError:
            pass
    assert json.loads((tmp_path / "other.json").read_text(encoding="utf-8"))["tries"] == 3
    assert digest.run(now=TUESDAY_10, **broken) == {"status": "not due"}              # three tries a morning


def test_the_saved_index_moves_are_read(tmp_path, monkeypatch):
    import run
    from tascreen.config import Settings

    monkeypatch.delenv("STATE_REPO_TOKEN", raising=False)
    settings = Settings(root=tmp_path)
    (settings.data_dir / "market").mkdir(parents=True)
    (settings.data_dir / "market" / "2026-09-28.json").write_text(json.dumps({"SPY": -0.42, "x": "y"}))
    assert run._saved_moves(settings, datetime(2026, 9, 28).date()) == {"SPY": -0.42}
    assert run._saved_moves(settings, datetime(2026, 9, 25).date()) == {}
    assert Path(digest_render.FONT).name == "Heebo-Variable.ttf"
