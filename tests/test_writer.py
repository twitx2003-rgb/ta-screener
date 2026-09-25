"""The written analysis: every section checked against the facts (made-up values)."""
from __future__ import annotations

import pytest

from tascreen.analyst import find_symbol, load_rules
from tascreen.analyst.facts import Analysis
from tascreen.analyst.writer import (PARTS, part_problem, allowed_numbers, fact_dates, system_prompt,
                                     telegram_html, write)
from tascreen.errors import ConfigError
from tascreen.llm import SyntheticLLM
from tascreen.notify import Telegram

RULES = load_rules()


def _analysis() -> Analysis:
    a = Analysis("NASDAQ:TEST", "2026-03-20", 0)
    a.facts = {
        "close": {"value": 104.2, "label": "סגירה אחרונה", "unit": "$"},
        "atr": {"value": 2.0, "label": "ATR", "unit": "$"},
        "zone_1.kind": {"value": "התנגדות", "label": "סוג", "unit": ""},
        "zone_1.low": {"value": 108.5, "label": "גבול תחתון", "unit": "$"},
        "zone_1.high": {"value": 110.0, "label": "גבול עליון", "unit": "$"},
        "zone_2.kind": {"value": "תמיכה", "label": "סוג", "unit": ""},
        "zone_2.low": {"value": 99.0, "label": "גבול תחתון", "unit": "$"},
        "zone_2.high": {"value": 100.5, "label": "גבול עליון", "unit": "$"},
        "ma.stack": {"value": "שורי (50 מעל 150 מעל 200, המחיר מעליהם)", "label": "סדר", "unit": ""},
        "volume.trend": {"value": "יציב", "label": "מגמת הנפח", "unit": ""},
        "div_1.kind": {"value": "דובית", "label": "סוג הסטייה", "unit": ""},
        "fib.end_day": {"value": "2026-02-13", "label": "תאריך סוף התנועה", "unit": ""},
        "fib_618": {"value": 101.9, "label": "תיקון פיבונאצ'י 61.8%", "unit": "$"},
    }
    a.drawings = {
        "zone_1": {"type": "zone", "kind": "resistance", "low": 108.5, "high": 110.0,
                   "first_day": "2026-01-05", "last_day": "2026-03-01", "touches": 3},
        "zone_2": {"type": "zone", "kind": "support", "low": 99.0, "high": 100.5,
                   "first_day": "2026-01-12", "last_day": "2026-03-10", "touches": 2}}
    return a


def _problem(text, cites, signal="yellow", part="levels"):
    facts = _analysis().facts
    return part_problem({"part": part, "signal": signal, "text": text, "cites": cites}, facts,
                        allowed_numbers(facts, RULES), fact_dates(facts))


def test_a_section_must_stay_inside_the_facts():
    assert _problem("התנגדות בין 108.5 ל-110, תמיכה סביב 100.5.", ["zone_1.low", "zone_2.high"]) is None
    assert _problem("פיבונאצ'י 61.8% ב-101.9, הנקודה מ-13/02/2026.", ["fib_618", "fib.end_day"]) is None
    assert "unknown" in _problem("התנגדות ב-110.", ["zone_9.high"])
    assert "cites no fact" in _problem("התנגדות ב-110.", [])
    assert "numbers" in _problem("המחיר עשוי להגיע ל-125.", ["zone_1.high"])       # invented level
    assert "dates" in _problem("השיא היה ב-14/02/2026.", ["fib.end_day"])           # a day off
    assert "advice" in _problem("כדאי לקנות מעל 110.", ["zone_1.high"])
    for leak in ("ההתנגדות (zone_1) ב-110.", "לפי ma.stack הסדר חיובי.", "הרמה fib_618 ב-101.9."):
        assert "internal ids" in _problem(leak, ["zone_1.high", "ma.stack", "fib_618"])
    assert _problem("ב-NVDA ובגרף 1D ההתנגדות ב-110.", ["zone_1.high"]) is None


def test_bullish_and_trend_words_need_a_fact_that_says_so():
    assert _problem("התמונה שורית: הממוצעים מסודרים.", ["ma.stack"]) is None
    assert "bullish" in _problem("התמונה שורית מעל 100.5.", ["zone_2.high"])
    assert "bearish" in _problem("והמבנה דובי מתחת ל-110.", ["ma.stack"])
    assert "falling trend" in _problem("המניה במגמה יורדת.", ["volume.trend"])


def test_the_headline_and_the_levels_must_use_the_charts_own_facts():
    facts = {**_analysis().facts,
             "level.r1.high": {"value": 110.0, "label": "התנגדות", "unit": "$"},
             "event": {"value": "המחיר בשיא של השנה האחרונה", "label": "האירוע", "unit": ""}}

    def problem(part, cites, text="סגירה מעל 110."):
        return part_problem({"part": part, "signal": "green", "text": text, "cites": cites},
                            facts, allowed_numbers(facts, RULES), fact_dates(facts))

    assert "level.* facts" in problem("levels", ["zone_1.high"])
    assert problem("levels", ["level.r1.high"]) is None
    assert "must cite event" in problem("headline", ["close"], "המחיר בשיא.")
    assert problem("headline", ["event"], "המחיר בשיא של השנה האחרונה.") is None
    assert "unknown section" in problem("up", ["level.r1.high"])       # the program writes it
    assert "forecast wording" in problem("levels", ["level.r1.high"], "סגירה מעל 110 תוביל למעלה.")
    assert problem("levels", ["level.r1.high"], "המחיר ירד אל 110.") is None   # past tense is fine


def test_the_light_must_not_contradict_the_text():
    assert _problem("התמונה שורית: הממוצעים מסודרים.", ["ma.stack"], "green", "trend") is None
    assert "green light on a bearish" in _problem("סטייה דובית במומנטום.", ["div_1.kind"], "green", "trend")
    assert _problem("סטייה דובית במומנטום.", ["div_1.kind"], "red", "trend") is None
    assert "red light on a bullish" in _problem("התמונה שורית.", ["ma.stack"], "red", "trend")
    assert "no light" in _problem("התנגדות ב-110.", ["zone_1.high"], None, "trend")
    # the levels' light is fixed (a neutral pin), whatever the model sent
    assert _problem("התנגדות ב-110.", ["zone_1.high"], "red", "levels") is None


def _parts(**texts):
    cites = {"headline": ["close"], "levels": ["zone_1.low", "zone_2.high"], "volume": ["volume.trend"],
             "trend": ["ma.stack"]}
    return {"parts": [{"part": k, "signal": "yellow", "text": v, "cites": cites[k]}
                      for k, v in texts.items()]}


GOOD = dict(headline="המחיר בין תמיכה להתנגדות.", levels="התנגדות 108.50-110.00, תמיכה 99.00-100.50.")


def test_a_rejected_or_missing_section_gets_one_retry_with_the_reasons():
    answers = [_parts(headline=GOOD["headline"], levels="התנגדות ב-777.", volume="הנפח 12345."),
               _parts(levels=GOOD["levels"], headline="שוב 555.", volume="הנפח יציב.")]
    llm = SyntheticLLM(lambda s, u, schema: answers.pop(0))
    written = write(_analysis(), llm, rules=RULES)
    assert [p["part"] for p in written["parts"]] == ["headline", "levels", "volume"]
    assert written["parts"][0]["text"] == GOOD["headline"]          # the first good one stays
    assert not written["omitted"] and len(written["usage"]) == 2
    retry = llm.calls[1].split("ONLY these")[1]
    assert "- levels: numbers not in the facts" in retry and "- volume: numbers" in retry
    assert "headline" not in retry                                    # optional ones get a rewrite too


def test_what_fails_twice_is_left_out_and_the_message_says_so():
    bad = _parts(**{**GOOD, "levels": "כדאי לקנות מעל 110."})
    written = write(_analysis(), SyntheticLLM(lambda s, u, schema: bad), rules=RULES)
    assert written["omitted"] == ["levels"]
    message = telegram_html(written)
    assert "הושמט: רמות" in message and "לא ייעוץ השקעות" in message and "לקנות" not in message
    lines = message.split("\n")
    assert lines[0] == "<b>📊 ניתוח טכני · TEST</b>" and lines[1] == "סגירה 104.20 · 20/03/2026"
    assert lines[3] == "🟡 " + GOOD["headline"]


def test_the_scenarios_are_written_by_the_program_from_the_facts():
    from tascreen.analyst.writer import scenario_parts

    def fact(v):
        return {"value": v, "label": "", "unit": ""}

    facts = {"up.trigger": fact(110.0), "up.trigger_pct": fact(5.6), "up.trigger_what": fact("אזור ההתנגדות"),
             "up.next": fact(120.0), "up.next_pct": fact(15.2), "up.next_what": fact("השיא השנתי"),
             "up.cancel": fact(108.5),
             "down.trigger": fact(99.0), "down.trigger_pct": fact(5.0), "down.trigger_what": fact("אזור התמיכה"),
             "down.no_next": fact("מתחתיה אין רמות ממחירי השנה האחרונה"), "down.cancel": fact(100.5)}
    up, down = scenario_parts(facts)
    assert up["text"] == ("בסגירה מעל 110.00 (אזור ההתנגדות, 5.6% מעל הסגירה), הרמה הבאה היא 120.00 "
                          "(השיא השנתי, 15.2% מעל הסגירה). התרחיש מתבטל בסגירה חזרה מתחת ל-108.50.")
    assert down["text"] == ("בסגירה מתחת ל-99.00 (אזור התמיכה, 5.0% מתחת לסגירה); מתחתיה אין רמות "
                            "ממחירי השנה האחרונה. התרחיש מתבטל בסגירה חזרה מעל 100.50.")
    assert (up["signal"], down["signal"]) == ("up", "down") and "up.trigger" in up["cites"]
    at_high = scenario_parts({"up.no_next": fact("המחיר בשיא השנתי; מעליו אין רמות ממחירי השנה האחרונה")})
    assert [p["part"] for p in at_high] == ["up"] and at_high[0]["text"].endswith("האחרונה.")


def test_at_most_two_optional_sections_and_the_ones_over_are_not_retried():
    answer = _parts(**GOOD, volume="הנפח יציב.")
    answer["parts"][2:2] = [
        {"part": "trend", "signal": "green", "text": "סדר הממוצעים שורי.", "cites": ["ma.stack"]},
        {"part": "fibonacci", "signal": "yellow", "text": "פיבונאצ'י 61.8% ב-101.90.", "cites": ["fib_618"]},
        {"part": "momentum", "signal": "green", "text": "המומנטום שורי.", "cites": ["close"]}]
    llm = SyntheticLLM(lambda s, u, schema: answer)
    written = write(_analysis(), llm, rules=RULES)
    optional = [p["part"] for p in written["parts"] if p["part"] not in ("headline", "levels", "up", "down")]
    assert optional == ["trend", "fibonacci"] and not written["omitted"]
    assert {d["reason"] for d in written["dropped"]} == {"more optional sections than allowed"}
    assert len(llm.calls) == 1                         # nothing failed a check: no retry


def test_the_message_is_escaped_and_fits_one_telegram_message():
    parts = [{"part": k, "title": PARTS[k], "text": "<b>" + "א" * 900, "cites": []} for k in PARTS]
    written = {"symbol": "NASDAQ:TEST", "last_day": "2026-03-20", "parts": parts, "omitted": []}
    message = telegram_html(written)
    assert len(message) <= 4096 and "&lt;b&gt;" in message
    kept = [k for k in PARTS if f"<b>{PARTS[k]}:</b>" in message]
    assert {"levels", "up", "down"} <= set(kept) and "momentum" not in kept    # the scenarios stay


def test_the_prompt_carries_the_knowledge_and_its_sources():
    prompt = system_prompt()
    assert "chartschool.stockcharts.com" in prompt and "tradingview.com/support" in prompt
    assert "גובה התבנית" in prompt and "must cite `event`" in prompt


def test_a_typed_symbol_is_found_among_the_stored_stocks(tmp_path):
    for stem in ("NASDAQ_TEST", "NYSE_ABC", "NASDAQ_ABC", "NYSE_BRK.B"):
        (tmp_path / f"{stem}.parquet").write_bytes(b"")
    assert find_symbol(tmp_path, " test ") == "NASDAQ:TEST"
    assert find_symbol(tmp_path, "$brk.b") == "NYSE:BRK.B"
    assert find_symbol(tmp_path, "nyse:abc") == "NYSE:ABC"
    for text in ("abc", "nope", "rm -rf /", "../x", "*"):
        with pytest.raises(ConfigError):
            find_symbol(tmp_path, text)


def test_photos_and_files_go_up_as_multipart():
    sent = []
    bot = Telegram("123456:" + "x" * 30, 42, upload=lambda m, p, f: sent.append((m, p, f)) or {"ok": True})
    bot.send_photo(b"PNG", "כותרת")
    bot.send_document(b"//@version=6", "a.pine", "סקריפט")
    assert [s[0] for s in sent] == ["sendPhoto", "sendDocument"]
    assert sent[0][1] == {"chat_id": 42, "caption": "כותרת"} and sent[0][2][:2] == ("photo", "chart.png")
    assert sent[1][2] == ("document", "a.pine", b"//@version=6", "text/plain")

    captured = {}
    real = Telegram("123456:" + "x" * 30, 42)
    real._request = lambda method, data, headers, timeout: captured.update(
        data=data, headers=headers) or {"ok": True}
    real.send_photo(b"\x89PNG-bytes", "caption")
    boundary = captured["headers"]["Content-Type"].split("boundary=")[1]
    body = captured["data"]
    assert body.startswith(f"--{boundary}\r\n".encode()) and body.endswith(f"--{boundary}--\r\n".encode())
    assert b'name="photo"; filename="chart.png"\r\nContent-Type: image/png\r\n\r\n\x89PNG-bytes' in body
