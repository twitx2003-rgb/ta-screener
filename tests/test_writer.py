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


def _problem(text, cites, signal="yellow", part="trend"):
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

    assert "must cite event" in problem("headline", ["close"], "המחיר בשיא.")
    assert problem("headline", ["event"], "המחיר בשיא של השנה האחרונה.") is None
    for part in ("levels", "up"):                                    # the program writes these (round 3)
        assert "unknown section" in problem(part, ["level.r1.high"])
    assert "forecast wording" in problem("trend", ["level.r1.high"], "סגירה מעל 110 תוביל למעלה.")
    assert problem("trend", ["level.r1.high"], "המחיר ירד אל 110.") is None   # past tense is fine


def test_the_light_must_not_contradict_the_text():
    assert _problem("התמונה שורית: הממוצעים מסודרים.", ["ma.stack"], "green", "trend") is None
    assert "green light on a bearish" in _problem("סטייה דובית במומנטום.", ["div_1.kind"], "green", "trend")
    assert _problem("סטייה דובית במומנטום.", ["div_1.kind"], "red", "trend") is None
    assert "red light on a bullish" in _problem("התמונה שורית.", ["ma.stack"], "red", "trend")
    assert "no light" in _problem("התנגדות ב-110.", ["zone_1.high"], None, "trend")
    assert "unknown section" in _problem("התנגדות ב-110.", ["zone_1.high"], "red", "levels")


def _parts(**texts):
    cites = {"headline": ["close", "ma.stack"], "volume": ["volume.trend"], "trend": ["ma.stack"], "levels": ["close"]}
    return {"parts": [{"part": k, "signal": "yellow", "text": v, "cites": cites[k]}
                      for k, v in texts.items()]}


GOOD = dict(headline="מגמת עלייה: המחיר בין תמיכה להתנגדות.", trend="הממוצעים מסודרים.")


def test_a_rejected_or_missing_section_gets_one_retry_with_the_reasons():
    answers = [_parts(headline=GOOD["headline"], trend="התנגדות ב-777.", volume="הנפח 12345."),
               _parts(trend=GOOD["trend"], headline="שוב 555.", volume="הנפח יציב.")]
    llm = SyntheticLLM(lambda s, u, schema: answers.pop(0))
    written = write(_analysis(), llm, rules=RULES)
    assert [p["part"] for p in written["parts"]] == ["headline", "trend", "volume"]
    assert written["parts"][0]["text"] == GOOD["headline"]          # the first good one stays
    assert not written["omitted"] and len(written["usage"]) == 2
    retry = llm.calls[1].split("ONLY these")[1]
    assert "- trend: numbers not in the facts" in retry and "- volume: numbers" in retry
    assert "headline" not in retry                                    # optional ones get a rewrite too


def test_what_fails_twice_is_left_out_and_the_message_says_so():
    bad = _parts(**{**GOOD, "headline": "כדאי לקנות מעל 110."})
    written = write(_analysis(), SyntheticLLM(lambda s, u, schema: bad), rules=RULES)
    assert written["omitted"] == ["headline"]
    message = telegram_html(written)
    assert "הושמט: בקצרה" in message and "אין באמור ייעוץ השקעות" in message and "לקנות" not in message
    good = write(_analysis(), SyntheticLLM(lambda s, u, schema: _parts(**GOOD)), rules=RULES, name="Test Corp.")
    lines = telegram_html(good).split("\n")
    assert lines[0] == "<b>📊 ניתוח טכני · TEST · Test</b>" and lines[1] == "סגירה 104.20 · 20/03/2026"
    # only the lead in bold, right to left; a blank line between sections (review round 3)
    assert lines[3] == "🟡\u200f <b>מגמת עלייה:</b> המחיר בין תמיכה להתנגדות." and lines[4] == ""


def test_the_scenarios_are_written_by_the_program_from_the_facts():
    from tascreen.analyst.writer import scenario_parts

    def fact(v):
        return {"value": v, "label": "", "unit": ""}

    facts = {"up.trigger": fact(110.0), "up.trigger_what": fact("הקצה העליון של ההתנגדות הקרובה"),
             "up.trigger_pct": fact(5.6), "up.next": fact(118.0), "up.next_far": fact(120.0),
             "up.next_what": fact("ההתנגדות הבאה"), "up.room_pct": fact(7.3), "up.cancel": fact(108.5),
             "up.cancel_what": fact("חזרה אל תוך האזור"), "up.risk_pct": fact(1.4),
             "down.trigger": fact(99.0), "down.trigger_what": fact("השפל השנתי"), "down.trigger_pct": fact(5.0),
             "down.no_next": fact("מתחתיו אין רמות מהשנה האחרונה"), "down.cancel": fact(100.5),
             "down.cancel_what": fact("הקצה התחתון של ההתנגדות הקרובה"), "down.risk_pct": fact(1.5)}
    up, down = scenario_parts(facts)
    # three short lines, each distance with its base (review round 3: "רמת הכניסה" read as
    # a trade entry, and the bases were unnamed)
    assert up["text"] == ("סגירה מעל 110.00, הקצה העליון של ההתנגדות הקרובה (5.6% מעל הסגירה).\n"
                          "הרמה הבאה: התנגדות בין 118.00 ל-120.00 (7.3% מעל רמת ההפעלה).\n"
                          "ביטול: סגירה חוזרת אל תוך האזור, מתחת ל-108.50 (1.4% מתחת לרמת ההפעלה).")
    assert down["text"] == ("סגירה מתחת ל-99.00, השפל השנתי (5.0% מתחת לסגירה).\n"
                            "מתחתיו אין רמות מהשנה האחרונה.\n"
                            "ביטול: סגירה חוזרת מעל 100.50, הקצה התחתון של ההתנגדות הקרובה (1.5% מעל רמת ההפעלה).")
    assert (up["signal"], down["signal"]) == ("up", "down") and "up.trigger" in up["cites"]
    assert "רמת הכניסה" not in up["text"] + down["text"]
    at_high = scenario_parts({"up.no_next": fact("המחיר בשיא השנתי; מעליו אין רמות מהשנה האחרונה")})
    assert [p["part"] for p in at_high] == ["up"] and at_high[0]["text"].endswith("האחרונה.")
    held = scenario_parts({"up.hold": fact(49.60), "up.hold_pattern": fact("משולש עולה"), "up.hold_pct": fact(1.2),
                           "up.next": fact(52.0), "up.next_what": fact("השיא השנתי"), "up.next_pct": fact(1.5)})
    assert held[0]["text"] == ("הפריצה נשמרת כל עוד המחיר נסגר מעל 49.60, קו הפריצה של תבנית משולש עולה "
                               "(1.2% מתחת לסגירה).\nהרמה הבאה: השיא השנתי, 52.00 (1.5% מעל הסגירה).\n"
                               "סגירה מתחת ל-49.60 מחזירה את המחיר אל תוך התבנית.")


def test_the_levels_line_is_written_by_the_program():
    from tascreen.analyst.writer import levels_part

    def fact(v):
        return {"value": v, "label": "", "unit": ""}

    facts = {"level.r1.low": fact(108.5), "level.r1.high": fact(111.0), "level.r1.what": fact("אזור ההתנגדות"),
             "level.r1.includes": fact("השיא השנתי, ממוצע 50 יום"), "level.r1.distance_pct": fact(4.1),
             "level.s1.low": fact(99.0), "level.s1.high": fact(99.0), "level.s1.what": fact("השפל השנתי"),
             "level.s1.distance_pct": fact(5.0), "level.s1.flipped": fact("תמיכה שנשברה ב-10/03, עכשיו התנגדות")}
    (part,) = levels_part(facts)
    assert part["text"] == ("התנגדות קרובה בין 108.50 ל-111.00 (כולל השיא השנתי וממוצע 50 יום), 4.1% מעל הסגירה. "
                            "תמיכה קרובה: השפל השנתי, 99.00, 5.0% מתחת לסגירה; תמיכה שנשברה ב-10/03, עכשיו התנגדות.")
    assert part["signal"] == "pin" and "level.r1.includes" in part["cites"]
    del facts["level.r1.low"]
    assert levels_part(facts)[0]["text"].startswith("מעל הסגירה אין התנגדות מהשנה האחרונה.")


def test_at_most_two_optional_sections_and_the_ones_over_are_not_retried():
    answer = _parts(headline=GOOD["headline"], volume="הנפח יציב.")
    answer["parts"][1:1] = [
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


def test_round_two_wording_rules_are_checked():
    facts = {**_analysis().facts, "rsi14": {"value": 55.0, "label": "RSI 14", "unit": ""},
             "pat_1.direction": {"value": "דובי", "label": "", "unit": ""}}

    def problem(part, text, cites, light="yellow"):
        return part_problem({"part": part, "signal": light, "text": text, "cites": cites},
                            facts, allowed_numbers(facts, RULES), fact_dates(facts))

    assert "שבירה" in problem("patterns", "המחיר פרץ מהתבנית.", ["pat_1.direction"])
    assert problem("patterns", "המחיר שבר כלפי מטה את קו התבנית.", ["pat_1.direction"]) is None
    assert "Latin" in problem("trend", "XYZ במגמה.", ["close"])
    # round 3: percent, never ATR; "מומנטום", never "תנופה"
    assert "jargon" in problem("trend", "המחיר 4.6 ATR מעל הממוצע.", ["close"])
    assert "jargon" in problem("trend", "התנופה חיובית.", ["close"])
    assert "neutral momentum" in problem("momentum", "ה-RSI ניטרלי.", ["rsi14"])
    facts["rsi14"]["value"] = 71.0
    assert "yellow" in problem("momentum", "ה-RSI בקניית יתר.", ["rsi14"], "green")
    assert problem("momentum", "ה-RSI בקניית יתר.", ["rsi14"], "yellow") is None


def test_dollars_and_this_years_dates_are_taken_out():
    from tascreen.analyst.writer import _finish

    assert _finish("התנגדות ב-110.00$ מ-10/09/2026, 5 דולר") == "התנגדות ב-110.00 מ-10/09, 5"
    assert _finish("יעד לפי גובה התבנית (לא תחזית) 120 - פיבונאצ'י") == "יעד לפי גובה התבנית 120, פיבונאצ׳י"


def test_an_undrawn_trendline_cannot_be_cited():
    facts = {**_analysis().facts, "tl_2.value": {"value": 120.0, "label": "", "unit": "$"}}
    problem = part_problem({"part": "trend", "signal": "yellow", "text": "קו מגמה ב-120.", "cites": ["tl_2.value"]},
                           facts, allowed_numbers(facts, RULES), fact_dates(facts), drawn={"tl_1"})
    assert "not on the chart" in problem
