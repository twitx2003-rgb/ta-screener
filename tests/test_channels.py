"""Discussion channels: selection, briefs, the post checks, generation, charts, pages.
Offline: a synthetic scan and a stand-in model (SyntheticLLM). Made-up prices."""
from __future__ import annotations

import json
import re
import xml.dom.minidom
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tascreen.channels.brief import topic_brief
from tascreen.channels.chart_svg import render
from tascreen.channels.generate import (ChannelWriter, _detection, banned, check_thread,
                                        load_personas, response_schema, unmatched_numbers)
from tascreen.channels.select import GENERAL, channels, pick_topics
from tascreen.config import ChannelsSettings
from tascreen.errors import ConfigError, ProviderError
from tascreen.llm import ClaudeCodeLLM, SyntheticLLM, UsageLimit
from tascreen.market_hours import next_sessions
from tascreen.web import fmt
from tascreen.web.fmt import post_text
from tascreen.web.app import HOST, create_app
from tascreen.web.data import ScanRepository
from test_web_live import RULES, _setup

PERSONAS = load_personas()


def _view(tmp_path):
    settings, store, day = _setup(tmp_path)
    return settings, store, ScanRepository(store, RULES).current()


def good_answer(system, user, schema):
    """A well-behaved model: cites real keys, quotes only fact values."""
    brief = json.loads(user.split("BRIEF (JSON):\n", 1)[1])
    threads = []
    for t in brief["topics"]:
        facts, tid = t["facts"], t["topic"]
        if tid == "recap":
            threads.append({"topic": "recap", "posts": [
                {"persona": "bookman", "kind": "reply", "reply_to": -1, "drawings": [], "note": "",
                 "text": f"היום {facts['scan.detections']['value']} זיהויים בסריקה.",
                 "cites": ["scan.detections"]}]})
            continue
        T = t["ticker"]
        close = facts[f"{T}.close"]["value"]
        threads.append({"topic": tid, "posts": [
            {"persona": "bookman", "kind": "chart", "reply_to": -1, "note": "קו האישור",
             "drawings": ["pivots", "confirm_line", "target", "bogus"],
             "text": f"{T} סגרה ב-{close}. תראו את התבנית, יש יעד למטה.",
             "cites": [f"{T}.close", f"{T}.pattern.name"]},
            {"persona": "rookie", "kind": "reply", "reply_to": 0, "drawings": [], "note": "",
             "text": "מה זה קו צוואר? 🤔", "cites": [f"{T}.pattern.name"]},
            {"persona": "skeptic", "kind": "reply", "reply_to": 1, "drawings": [], "note": "",
             "text": "לא בטוח שהקונים באמת שם.", "cites": [f"{T}.rel_volume"]}]})
    return {"threads": threads}


def _writer(store, llm=None, **cfg):
    return ChannelWriter(store=store, rules=RULES, cfg=ChannelsSettings(**cfg),
                         llm=llm or SyntheticLLM(good_answer), personas=PERSONAS)


# ------------------------------------------------------------------ selection
def test_channels_are_the_most_common_patterns_after_general(tmp_path):
    _, _, view = _view(tmp_path)
    found = channels(view.detections, RULES, 8)
    assert found[0].id == GENERAL and found[0].name == "#כללי"
    counts = [c.count for c in found[1:]]
    assert counts == sorted(counts, reverse=True)
    assert channels(view.detections, RULES, 1)[1:] == found[1:2]


def test_topics_put_breakouts_before_forming_and_one_per_stock(tmp_path):
    _, _, view = _view(tmp_path)
    picked = pick_topics(view.stocks, view.detections, GENERAL, 5)
    assert picked and all(d["status"] == "breakout" and d["family"] == "chart" for d in picked)
    forming = pick_topics(view.stocks, view.detections, "double_bottom", 3)
    assert [d["symbol"] for d in forming] == ["NASDAQ:DB"]


def test_brief_has_the_trigger_of_a_forming_pattern_and_live_facts(tmp_path):
    _, _, view = _view(tmp_path)
    det = _detection(pick_topics(view.stocks, view.detections, "double_bottom", 1)[0])
    brief = topic_brief(view.stock("NASDAQ:DB"), det, "t1",
                        live={"price": 113.0, "change_pct": 2.5, "level": 112.3, "direction": "bullish"})
    facts, T = brief["facts"], brief["ticker"]
    assert f"{T}.pattern.trigger_up" in facts and f"{T}.live.crossing_level" in facts
    assert f"{T}.sma150" in facts or view.stock("NASDAQ:DB")["sma150"] != view.stock("NASDAQ:DB")["sma150"]
    assert "nan" not in json.dumps(facts).lower()
    assert any(k.startswith(f"{T}.check.") for k in facts)


# --------------------------------------------------------------------- checks
FACTS = {"X.close": {"value": 123.45, "label": "סגירה", "unit": "$"},
         "X.check.min_rise_between_pct": {"value": 12.6, "label": "עלייה (סף >= 10.0; עבר)", "unit": ""},
         "X.pattern.name": {"value": "תחתית כפולה", "label": "התבנית", "unit": ""}}


def _post(text, **kw):
    return {"persona": "bookman", "kind": "chart", "reply_to": -1, "text": text, "drawings": ["pivots"],
            "note": "", "cites": ["X.close"], **kw}


@pytest.mark.parametrize("post, reason", [
    (_post("X סגרה ב-123.45, מעל הקו"), None),
    (_post("X סגרה ב-123.5 עם עלייה של 12.6% (הסף 10)"), None),         # rounding, thresholds
    (_post("ממוצע 150 ו-RSI 14 ביום 22/09"), None),                    # periods and dates
    (_post("X סגרה ב-131.20"), "numbers not in the facts"),
    (_post("סגירה ב-123.45", cites=[]), "cites no fact"),
    (_post("סגירה ב-123.45", cites=["X.nope"]), "cites unknown fact keys"),
    (_post("סגירה ב-123.45", persona="guru"), "unknown persona"),
    (_post("תקנו עכשיו, 123.45"), "advice or forecast wording"),
    (_post("זה הולך לעלות"), "advice or forecast wording"),
    (_post("קניתי ב-123.45"), "advice or forecast wording"),
    (_post("לא בטוח. הקונים חלשים"), None),                             # doubt is allowed
    (_post("ok", note="יעד 999"), "numbers not in the facts"),
])
def test_post_checks(post, reason):
    kept, dropped = check_thread({"topic": "t1", "posts": [post]}, {"facts": FACTS}, PERSONAS,
                                 live=False, recap=False)
    if reason is None:
        assert kept and not dropped
    else:
        assert not kept and reason in dropped[0]["reason"]


def test_a_thread_must_open_with_its_chart():
    reply = _post("X סגרה ב-123.45", kind="reply")
    kept, dropped = check_thread({"topic": "t1", "posts": [reply, _post("X ב-123.45")]},
                                 {"facts": FACTS}, PERSONAS, live=False, recap=False)
    assert kept == [] and "chart post" in dropped[0]["reason"]


def test_targets_and_live_crossings_get_their_caveats():
    kept, _ = check_thread({"topic": "t1", "posts": [_post("יש יעד מעל 123.45", drawings=["pivots"])]},
                           {"facts": FACTS}, PERSONAS, live=True, recap=False)
    text = kept[0]["text"]
    assert "כלל המדידה" in text and "לא סופי עד הסגירה" in text
    assert "trigger" in kept[0]["drawings"]


def test_hebrew_prefixes_do_not_hide_advice():
    assert banned("וקנו את זה") and banned("שתקנו") and not banned("הקונים והמוכרים")


def test_numbers_inside_ticker_like_tokens_are_ignored():
    assert unmatched_numbers("SMA150 ו-SMA50 מעל, EMA200", []) == []


def test_schema_limits_personas_topics_and_drawings():
    schema = response_schema(["a", "b"], ["t1"])
    post = schema["properties"]["threads"]["items"]["properties"]["posts"]["items"]
    assert post["properties"]["persona"]["enum"] == ["a", "b"]
    assert "zone" in post["properties"]["drawings"]["items"]["enum"]


# ----------------------------------------------------------------- generation
def test_daily_writes_every_channel_with_charts_and_skips_on_rerun(tmp_path):
    settings, store, view = _view(tmp_path)
    llm = SyntheticLLM(good_answer)
    report = _writer(store, llm).daily(view, progress=lambda m: None)
    written = {k: v for k, v in report["channels"].items() if isinstance(v, dict)}
    assert GENERAL in written and "head_shoulders_top" in written
    doc = store.read_channel_doc(view.day, "head_shoulders_top")
    thread = doc["threads"][0]
    assert [p["kind"] for p in thread["posts"]] == ["chart", "reply", "reply"]
    assert thread["posts"][0]["drawings"] == ["pivots", "confirm_line", "target"]   # "bogus" dropped
    assert "כלל המדידה" in thread["posts"][0]["text"]
    svg = store.read_channel_chart(view.day, thread["posts"][0]["chart"])
    xml.dom.minidom.parseString(svg)
    general = store.read_channel_doc(view.day, GENERAL)
    assert any(t["symbol"] is None and t["posts"][0]["text"].startswith("היום") for t in general["threads"])

    calls = len(llm.calls)
    again = _writer(store, llm).daily(view, progress=lambda m: None)
    assert len(llm.calls) == calls and set(again["channels"].values()) <= {"already written", "nothing to discuss"}
    _writer(store, llm).daily(view, force=True, only={GENERAL}, progress=lambda m: None)
    assert len(llm.calls) == calls + 1


def test_a_model_failure_leaves_the_channel_for_the_next_run(tmp_path):
    _, store, view = _view(tmp_path)

    def broken(system, user, schema):
        raise ProviderError("the model is down")

    report = _writer(store, SyntheticLLM(broken)).daily(view, only={GENERAL}, progress=lambda m: None)
    assert report["channels"][GENERAL].startswith("failed") and store.read_channel_doc(view.day, GENERAL) is None


def test_live_crossings_post_once_within_the_hourly_budget(tmp_path):
    _, store, view = _view(tmp_path)
    session = next_sessions(view.day, 1)[0]
    cross = {"NASDAQ:DB": [{"pattern": "double_bottom", "direction": "bullish", "level": 112.3, "price": 113.0}]}
    llm = SyntheticLLM(good_answer)
    first = _writer(store, llm).live(view, session, {"NASDAQ:DB": 113.0}, {"NASDAQ:DB": 2.5}, cross,
                                     progress=lambda m: None)
    assert first["written"] == 1
    doc = store.read_channel_doc(session, "live")
    thread = doc["threads"][0]
    assert thread["live"] and thread["channel"] == "double_bottom"
    assert "trigger" in thread["posts"][0]["drawings"] and "לא סופי עד הסגירה" in thread["posts"][0]["text"]
    again = _writer(store, llm).live(view, session, {"NASDAQ:DB": 113.0}, {}, cross, progress=lambda m: None)
    assert again["written"] == 0 and len(llm.calls) == 1
    none_left = _writer(store, llm, live_max_per_hour=0).live(
        view, session, {}, {}, {"NASDAQ:DB": [{**cross["NASDAQ:DB"][0], "pattern": "other"}]},
        progress=lambda m: None)
    assert none_left["written"] == 0


# ------------------------------------------------------------------- the CLI
def test_claude_code_is_refused_inside_claude_code():
    with pytest.raises(ConfigError, match="normal terminal"):
        ClaudeCodeLLM(model="sonnet", effort="medium", command=["claude"], environ={"CLAUDECODE": "1"})


def test_claude_code_runs_without_api_keys_and_reads_structured_output():
    seen = {}

    def fake_run(args, **kw):
        seen["args"], seen["env"] = args, kw["env"]
        out = {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"threads": []},
               "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01,
               "modelUsage": {"claude-sonnet-5": {}}}
        return SimpleNamespace(stdout=json.dumps(out).encode(), stderr=b"", returncode=0)

    llm = ClaudeCodeLLM(model="sonnet", effort="medium", command=["claude.exe"], run=fake_run,
                        environ={"ANTHROPIC_API_KEY": "secret", "PATH": "x"})
    parsed, usage = llm.complete(system="s", user="u", schema={"type": "object"})
    assert parsed == {"threads": []} and usage["api_equivalent_cost_usd"] == 0.01
    assert "ANTHROPIC_API_KEY" not in seen["env"] and seen["env"]["PATH"] == "x"
    assert seen["args"][seen["args"].index("--tools") + 1] == "" and "--json-schema" in seen["args"]
    assert "--bare" not in seen["args"]


def test_claude_code_errors_are_provider_errors():
    def failing(args, **kw):
        return SimpleNamespace(stdout=json.dumps({"type": "result", "subtype": "error_max_turns",
                                                  "is_error": True}).encode(), stderr=b"", returncode=1)

    llm = ClaudeCodeLLM(model="sonnet", effort="medium", command=["c"], run=failing, environ={})
    with pytest.raises(ProviderError, match="error_max_turns"):
        llm.complete(system="s", user="u", schema={})


# ---------------------------------------------------------------------- chart
def test_chart_svg_is_valid_and_draws_what_was_asked(tmp_path):
    _, store, view = _view(tmp_path)
    det = _detection(pick_topics(view.stocks, view.detections, "head_shoulders_top", 1)[0])
    bars = store.read_bars(det["symbol"])
    plain = render(bars, det, [], seed="a", title="NYSE:HS")
    drawn = render(bars, det, ["pivots", "confirm_line", "breakout", "target", "zone", "volume", "sma"],
                   "קו הצוואר", seed="a", title="NYSE:HS")
    for svg in (plain, drawn):
        xml.dom.minidom.parseString(svg)
        assert not re.search(r"(?<![a-z])nan(?![a-z])", svg.lower()) and "None" not in svg
    assert drawn.count("<path") > plain.count("<path") and "קו הצוואר" in drawn and "יעד" in drawn
    assert render(bars, det, ["pivots"], seed="a", title="t") == render(bars, det, ["pivots"], seed="a", title="t")


# ---------------------------------------------------------------------- pages
def test_channel_pages_show_the_threads_as_ai_agents(tmp_path):
    settings, store, view = _view(tmp_path)
    _writer(store).daily(view, progress=lambda m: None)
    client = TestClient(create_app(settings, rules=RULES), base_url=f"http://{HOST}")
    for url in ("/", "/c/head_shoulders_top", "/c/double_bottom"):
        html = client.get(url).text
        assert fmt.page_problems(html) == [], url
        assert "סוכני AI" in html
    page = client.get("/c/head_shoulders_top").text
    posts = page.count('class="msg ')
    assert posts >= 3 and page.count("סוכן AI</span>") == posts
    assert "<svg" in page and "בתגובה ל" in page and "לקריאה בלבד" in page
    listing = client.get("/api/channels").json()
    assert listing["channels"][0]["id"] == GENERAL and "AI agents" in listing["note"]
    api = client.get("/api/channels/head_shoulders_top").json()
    assert api["threads"][0]["posts"][0]["ai_agent"] is True
    assert client.get("/api/channels/nope").status_code == 404


def test_new_posts_change_the_page_stamp(tmp_path):
    settings, store, view = _view(tmp_path)
    client = TestClient(create_app(settings, rules=RULES), base_url=f"http://{HOST}")
    before = client.get("/api/stamp").json()["stamp"]
    _writer(store).daily(view, only={GENERAL}, progress=lambda m: None)
    assert client.get("/api/stamp").json()["stamp"] != before


LIMIT_RESULT = {"type": "result", "subtype": "success", "is_error": True,
                "result": "You've hit your session limit · resets 11:50pm (Asia/Jerusalem)"}


def test_the_usage_limit_is_its_own_error():
    def limited(args, **kw):
        return SimpleNamespace(stdout=json.dumps(LIMIT_RESULT).encode(), stderr=b"", returncode=1)

    llm = ClaudeCodeLLM(model="sonnet", effort="medium", command=["c"], run=limited, environ={})
    with pytest.raises(UsageLimit, match="session limit"):
        llm.complete(system="s", user="u", schema={})


def test_after_the_usage_limit_the_other_channels_wait_for_the_next_run(tmp_path):
    _, store, view = _view(tmp_path)

    def limited(system, user, schema):
        raise UsageLimit("Claude usage limit reached")

    llm = SyntheticLLM(limited)
    report = _writer(store, llm).daily(view, progress=lambda m: None)
    results = list(report["channels"].values())
    assert results[0].startswith("failed") and all(r.startswith("skipped") for r in results[1:])
    assert len(llm.calls) == 1


def test_live_posts_pause_for_an_hour_after_the_usage_limit(tmp_path):
    _, store, view = _view(tmp_path)
    session = next_sessions(view.day, 1)[0]
    cross = {"NASDAQ:DB": [{"pattern": "double_bottom", "direction": "bullish", "level": 112.3, "price": 113.0}]}

    def limited(system, user, schema):
        raise UsageLimit("Claude usage limit reached")

    writer = _writer(store, SyntheticLLM(limited))
    writer.live(view, session, {}, {}, cross, progress=lambda m: None)
    assert writer.paused_until is not None
    again = writer.live(view, session, {}, {}, cross, progress=lambda m: None)
    assert "paused_until" in again and len(writer.llm.calls) == 1


def test_numbers_keep_their_units_in_right_to_left_text():
    html = str(post_text("נפח יחסי 1.11x, עלייה 12.6%, שווי 25.3B, מחיר $29.38, S0001"))
    for token in ("1.11x", "12.6%", "25.3B", "$29.38"):
        assert f'<bdi class="num">{token}</bdi>' in html
    assert "S0001" in html and '<bdi class="num">0001' not in html
