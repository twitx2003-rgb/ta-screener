"""Breaking news from X: reading (a fake reader), Claude's picks (SyntheticLLM), the
message, and what is remembered between runs. Synthetic posts only."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tascreen import xnews
from tascreen.config import XNewsSettings
from tascreen.errors import ConfigError, ProviderError
from tascreen.llm import SyntheticLLM

KEY = "fake-x-key-for-tests-0123456789"
NOW = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


def row(post_id, author="NewsDesk", text="Company A raises guidance", reply=False):
    return {"id": post_id, "text": text, "url": f"https://x.com/{author}/status/{post_id}",
            "createdAt": "Mon Jan 05 14:58:00 +0000 2026", "isReply": reply,
            "author": {"userName": author}}


class FakeReader:
    """GET /twitter/tweet/advanced_search: the given pages, one after another."""

    def __init__(self, *pages):
        self.pages, self.queries = list(pages), []

    def __call__(self, path, params):
        assert path == "/twitter/tweet/advanced_search"
        self.queries.append(params)
        return self.pages.pop(0) if self.pages else {"tweets": [], "has_next_page": False}


def picker(*picks):
    return SyntheticLLM(lambda system, user, schema: {"picks": list(picks)})


def run(tmp_path, reader, llm, sent, accounts=("NewsDesk",), **kw):
    made = []

    def factory():
        made.append(llm)
        return llm

    summary = xnews.run_once(accounts=list(accounts), source=xnews.XSource(KEY, get=reader),
                             llm_factory=factory, send=sent.append, state_path=tmp_path / "state.json",
                             now=kw.pop("now", NOW), min_importance=kw.pop("min_importance", 4),
                             daily_read_cap=kw.pop("daily_read_cap", 1000),
                             fetch=kw.pop("fetch", lambda url: None), **kw)       # never online
    return summary, made


def test_only_important_posts_are_sent_once(tmp_path):
    page = {"tweets": [row("11"), row("12", text="gm everyone"), row("13", reply=True)],
            "has_next_page": False}
    llm = picker({"post_id": "11", "importance": 5, "summary_he": "חברה A העלתה תחזית <חשוב>"},
                 {"post_id": "12", "importance": 2, "summary_he": "ברכת בוקר"})
    sent = []
    summary, _ = run(tmp_path, FakeReader(page), llm, sent)
    assert summary["read"] == 2 and summary["new"] == 2 and summary["sent"] == 1   # the reply is skipped
    assert len(sent) == 1 and "https://x.com/NewsDesk/status/11" in sent[0]
    assert "&lt;חשוב&gt;" in sent[0] and "ברכת בוקר" not in sent[0]              # escaped, and 2 < 4
    # the same posts again: nothing new, so no model call and no message
    summary, made = run(tmp_path, FakeReader(page), llm, sent)
    assert summary["new"] == 0 and made == [] and len(sent) == 1


def test_an_invented_or_broken_pick_is_rejected(tmp_path):
    llm = picker({"post_id": "999", "importance": 5, "summary_he": "לא קיים"},
                 {"post_id": "21", "importance": 9, "summary_he": "ציון שבור"},
                 {"post_id": "22", "importance": 4, "summary_he": ""})
    sent = []
    summary, _ = run(tmp_path, FakeReader({"tweets": [row("21"), row("22")], "has_next_page": False}), llm, sent)
    assert summary["rejected"] == 3 and summary["sent"] == 0 and sent == []


def test_accounts_are_grouped_and_pages_followed(tmp_path):
    accounts = [f"acct{n}" for n in range(12)]
    reader = FakeReader({"tweets": [row("31")], "has_next_page": True, "next_cursor": "c2"},
                        {"tweets": [row("32")], "has_next_page": False},
                        {"tweets": [row("33", author="acct11")], "has_next_page": False})
    summary, _ = run(tmp_path, reader, picker(), [], accounts=accounts)
    assert summary["calls"] == 3 and summary["read"] == 3
    assert reader.queries[0]["query"].startswith("(from:acct0 OR from:acct1") and "cursor" not in reader.queries[0]
    assert reader.queries[1]["cursor"] == "c2" and "from:acct11" in reader.queries[2]["query"]
    since = int(NOW.timestamp()) - 900 - xnews.OVERLAP_S
    assert reader.queries[0]["query"].endswith(f"since_time:{since} -filter:replies")
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["since"] == int(NOW.timestamp()) and state["seen"] == ["31", "32", "33"]


def test_a_failed_send_is_retried_next_run(tmp_path):
    page = {"tweets": [row("41")], "has_next_page": False}
    llm = picker({"post_id": "41", "importance": 5, "summary_he": "חדשה"})

    def broken(text):
        raise ProviderError("Telegram sendMessage: HTTP 502")

    with pytest.raises(ProviderError):
        xnews.run_once(accounts=["NewsDesk"], source=xnews.XSource(KEY, get=FakeReader(page)),
                       llm_factory=lambda: llm, send=broken, state_path=tmp_path / "state.json",
                       now=NOW, min_importance=4, daily_read_cap=1000)
    sent = []
    summary, _ = run(tmp_path, FakeReader(page), llm, sent)
    assert summary["sent"] == 1 and len(sent) == 1


def test_the_daily_cap_stops_reading(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"reads": {"day": "2026-01-05", "posts": 50}}),
                                         encoding="utf-8")
    reader = FakeReader()
    summary, _ = run(tmp_path, reader, picker(), [], daily_read_cap=50)
    assert summary["read_status"] == "daily cap" and reader.queries == []
    summary, _ = run(tmp_path, reader, picker(), [], daily_read_cap=50,
                     now=datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc))      # a new day
    assert "read_status" not in summary and summary["status"] == "ok"


def test_claude_is_asked_every_20_minutes_at_most(tmp_path):
    """Posts read in between wait and are judged together; the daily cap is kept."""
    llm = picker({"post_id": "52", "importance": 5, "summary_he": "חדשה"})
    sent = []
    first, made = run(tmp_path, FakeReader({"tweets": [row("51")], "has_next_page": False}), llm, sent,
                      llm_min_interval_s=1200)
    assert first["judged"] == 1 and len(made) == 1
    ten = datetime(2026, 1, 5, 15, 10, tzinfo=timezone.utc)
    waiting, made = run(tmp_path, FakeReader({"tweets": [row("52")], "has_next_page": False}), llm, sent,
                        now=ten, llm_min_interval_s=1200)
    assert waiting["judged"] == 0 and waiting["waiting"] == 1 and made == [] and sent == []
    twenty = datetime(2026, 1, 5, 15, 20, tzinfo=timezone.utc)
    later, made = run(tmp_path, FakeReader({"tweets": [row("53")], "has_next_page": False}), llm, sent,
                      now=twenty, llm_min_interval_s=1200)
    assert later["judged"] == 2 and later["sent"] == 1 and "52" in llm.calls[-1] and "53" in llm.calls[-1]
    assert later["claude_today"] == 2
    capped, made = run(tmp_path, FakeReader({"tweets": [row("54")], "has_next_page": False}), llm, sent,
                       now=datetime(2026, 1, 5, 16, 0, tzinfo=timezone.utc), llm_daily_cap=2)
    assert capped["status"] == "claude daily cap" and made == [] and capped["waiting"] == 1


def test_the_usage_limit_pauses_claude_for_an_hour(tmp_path):
    from tascreen.llm import UsageLimit

    def limited(system, user, schema):
        raise UsageLimit("Claude usage limit reached")

    page = {"tweets": [row("61")], "has_next_page": False}
    summary, _ = run(tmp_path, FakeReader(page), SyntheticLLM(limited), [])
    assert summary["status"] == "claude limit" and summary["waiting"] == 1
    llm = picker({"post_id": "61", "importance": 5, "summary_he": "חדשה"})
    soon, made = run(tmp_path, FakeReader(), llm, [], now=datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc))
    assert made == [] and soon["waiting"] == 1
    sent = []
    back, _ = run(tmp_path, FakeReader(), llm, sent, now=datetime(2026, 1, 5, 16, 1, tzinfo=timezone.utc))
    assert back["sent"] == 1 and len(sent) == 1


def test_a_changed_answer_names_the_real_keys_and_hides_the_key():
    with pytest.raises(ProviderError, match="Actual keys: \\['data', 'more'\\]"):
        xnews.XSource(KEY, get=lambda p, q: {"data": [], "more": False}).new_posts(["NewsDesk"], 0)
    with pytest.raises(ProviderError) as caught:
        xnews.XSource(KEY, get=lambda p, q: {"status": "error", "msg": f"bad key {KEY}"}).new_posts(["a"], 0)
    assert KEY not in str(caught.value) and "<key>" in str(caught.value)


def test_the_key_comes_from_the_environment_or_the_saved_file(tmp_path, monkeypatch):
    assert xnews.key_from_environment() is None
    monkeypatch.setattr(xnews, "CREDENTIALS", tmp_path / "x.json")
    xnews.save_key(" saved-key ")
    assert xnews.key_from_environment() == "saved-key"
    monkeypatch.setenv("X_API_KEY", "env-key")
    assert xnews.key_from_environment() == "env-key"


def test_account_names_are_checked():
    assert XNewsSettings(accounts=["@NewsDesk", "Other_1", "NewsDesk"]).accounts == ("NewsDesk", "Other_1")
    for bad in (["not an account"], "NewsDesk", ["x" * 16]):
        with pytest.raises(ConfigError):
            XNewsSettings(accounts=bad)
    with pytest.raises(ConfigError):
        XNewsSettings(min_importance=6)


def test_a_long_message_fits_telegram():
    picks = [(xnews.to_post(row(str(n))), {"importance": 4, "summary_he": "א" * 400}) for n in range(20)]
    assert len(xnews.message(picks)) <= 4000


def test_after_three_months_the_owner_is_told_once_and_nothing_is_read(tmp_path):
    sent = []
    kw = dict(until="2026-01-05", state_path=tmp_path / "state.json", send=sent.append)
    assert not xnews.past_end(now=NOW, **kw) and sent == []                   # the last day still runs
    later = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
    assert xnews.past_end(now=later, **kw) and xnews.past_end(now=later, **kw)
    assert sent == [xnews.ENDED_TEXT]
    with pytest.raises(ConfigError):
        XNewsSettings(until="three months")


def test_an_empty_page_ends_the_paging_even_if_more_are_offered(tmp_path):
    reader = FakeReader({"tweets": [row("71")], "has_next_page": True, "next_cursor": "c2"},
                        {"tweets": [], "has_next_page": True, "next_cursor": "c3"})
    summary, _ = run(tmp_path, reader, picker(), [])
    assert summary["calls"] == 2 and summary["read"] == 1


def photo_row(post_id, kind="photo", url="https://pbs.twimg.com/media/synthetic.jpg"):
    return {**row(post_id), "extendedEntities": {"media": [{"type": kind, "media_url_https": url}]}}


def test_only_a_real_photo_is_taken():
    assert xnews.to_post(photo_row("81")).photo == "https://pbs.twimg.com/media/synthetic.jpg"
    assert xnews.to_post(photo_row("82", kind="video")).photo == ""
    assert xnews.to_post(photo_row("83", url="https://evil.example/x.jpg")).photo == ""
    assert xnews.to_post(row("84")).photo == "" and xnews.to_post({**row("85"), "extendedEntities": None}).photo == ""


def test_a_pick_with_a_photo_goes_as_a_photo_and_the_rest_as_one_short_message(tmp_path):
    page = {"tweets": [photo_row("91"), row("92")], "has_next_page": False}
    llm = picker({"post_id": "91", "importance": 5, "summary_he": "חדשה עם גרף"},
                 {"post_id": "92", "importance": 4, "summary_he": "חדשה בלי תמונה"})
    sent, photos = [], []
    summary, _ = run(tmp_path, FakeReader(page), llm, sent, send_photo=lambda u, c: photos.append((u, c)))
    assert summary["sent"] == 2 and summary["photos"] == 1
    assert sent == ['🟠 <b>NewsDesk</b>: חדשה בלי תמונה <a href="https://x.com/NewsDesk/status/92">↗</a>']
    assert photos == [("https://pbs.twimg.com/media/synthetic.jpg",
                       '🔴 <b>NewsDesk</b>: חדשה עם גרף <a href="https://x.com/NewsDesk/status/91">↗</a>')]


def test_a_photo_telegram_cannot_fetch_goes_as_text(tmp_path):
    def refused(url, caption):
        raise ProviderError("Telegram sendPhoto: wrong file identifier")

    sent = []
    summary, _ = run(tmp_path, FakeReader({"tweets": [photo_row("95")], "has_next_page": False}),
                     picker({"post_id": "95", "importance": 5, "summary_he": "חדשה"}), sent, send_photo=refused)
    assert summary["photos"] == 0 and len(sent) == 1 and "status/95" in sent[0]


def test_a_waiting_post_keeps_its_photo(tmp_path):
    llm = picker({"post_id": "97", "importance": 5, "summary_he": "חדשה"})
    run(tmp_path, FakeReader({"tweets": [row("96")], "has_next_page": False}), llm, [], llm_min_interval_s=1200)
    run(tmp_path, FakeReader({"tweets": [photo_row("97")], "has_next_page": False}), llm, [],
        now=datetime(2026, 1, 5, 15, 10, tzinfo=timezone.utc), llm_min_interval_s=1200)       # waits
    photos = []
    run(tmp_path, FakeReader(), llm, [], now=datetime(2026, 1, 5, 15, 20, tzinfo=timezone.utc),
        llm_min_interval_s=1200, send_photo=lambda u, c: photos.append(u))
    assert photos == ["https://pbs.twimg.com/media/synthetic.jpg"]


def test_a_chart_gets_one_line_saying_what_it_shows(tmp_path):
    def answer(system, user, schema):
        if "images" in schema["properties"]:
            return {"images": [{"post_id": "101", "image_he": "גרף של תשואת האג\"ח ל-10 שנים בשנה האחרונה"},
                               {"post_id": "999", "image_he": "המצאה"}]}
        return {"picks": [{"post_id": "101", "importance": 5, "summary_he": "התשואה קפצה"},
                          {"post_id": "102", "importance": 5, "summary_he": "תמונה של מנכ\"ל"}]}

    llm = SyntheticLLM(answer)
    photos = []
    page = {"tweets": [photo_row("101"), photo_row("102")], "has_next_page": False}
    summary, _ = run(tmp_path, FakeReader(page), llm, [], send_photo=lambda u, c: photos.append(c),
                     fetch=lambda url: ("image/jpeg", b"synthetic"))
    assert summary["explained"] == 1 and llm.images == [2] and summary["claude_today"] == 2
    assert photos[0].endswith("\n📊 גרף של תשואת האג&quot;ח ל-10 שנים בשנה האחרונה")
    assert "📊" not in photos[1]                                        # not a chart: no line


def test_the_news_goes_out_even_if_the_picture_look_fails(tmp_path):
    def answer(system, user, schema):
        if "images" in schema["properties"]:
            raise ProviderError("Claude Code returned an error (error_during_execution): x")
        return {"picks": [{"post_id": "111", "importance": 5, "summary_he": "חדשה"}]}

    photos = []
    summary, _ = run(tmp_path, FakeReader({"tweets": [photo_row("111")], "has_next_page": False}),
                     SyntheticLLM(answer), [], send_photo=lambda u, c: photos.append(c),
                     fetch=lambda url: ("image/png", b"synthetic"))
    assert summary["explain_error"] == "ProviderError" and len(photos) == 1 and "📊" not in photos[0]


def test_only_pictures_from_x_are_fetched():
    assert xnews.fetch_image("https://evil.example/x.jpg") is None


def test_a_short_analysis_goes_under_the_news_unless_it_gives_advice(tmp_path):
    llm = picker({"post_id": "121", "importance": 5, "summary_he": "חברה A העלתה תחזית",
                  "analysis_he": "העלאת תחזית מצביעה על ביקוש חזק; מניות הענף רגישות לזה"},
                 {"post_id": "122", "importance": 5, "summary_he": "חברה B הורידה תחזית",
                  "analysis_he": "כדאי למכור את המניה"})
    sent = []
    run(tmp_path, FakeReader({"tweets": [row("121"), row("122")], "has_next_page": False}), llm, sent)
    first, second = sent[0].split("\n\n")
    assert first.endswith("↗</a>\n💡 העלאת תחזית מצביעה על ביקוש חזק; מניות הענף רגישות לזה")
    assert "💡" not in second and "למכור" not in second


def test_on_the_weekend_only_the_dramatic_goes_out(tmp_path):
    llm = picker({"post_id": "131", "importance": 3, "summary_he": "עדכון רגיל"},
                 {"post_id": "132", "importance": 4, "summary_he": "חשוב לשבוע הבא"})
    page = {"tweets": [row("131"), row("132")], "has_next_page": False}
    saturday = datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc)
    sent = []
    run(tmp_path, FakeReader(page), llm, sent, now=saturday, min_importance=3)
    assert "חשוב לשבוע הבא" in sent[0] and "עדכון רגיל" not in sent[0]
    assert llm.calls[-1].startswith("It is the weekend")
    sent = []
    run(tmp_path / "weekday", FakeReader(page), llm, sent, min_importance=3)        # a Monday
    assert "עדכון רגיל" in sent[0] and not llm.calls[-1].startswith("It is the weekend")


def test_after_a_night_only_the_last_hour_is_read(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"since": int(NOW.timestamp()) - 17 * 3600}),
                                         encoding="utf-8")
    reader = FakeReader()
    run(tmp_path, reader, picker(), [])
    since = int(NOW.timestamp()) - xnews.MAX_LOOKBACK_S - xnews.OVERLAP_S
    assert f"since_time:{since} " in reader.queries[0]["query"]


def test_one_story_from_three_accounts_goes_out_once_with_its_sources(tmp_path):
    page = {"tweets": [row("141", author="DeItaone"), row("142", author="zerohedge"),
                       row("143", author="markets")], "has_next_page": False}
    llm = picker({"post_id": "141", "importance": 5, "summary_he": "המדד עלה", "analysis_he": "",
                  "same_story_as": "", "repeat_of_sent": False},
                 {"post_id": "142", "importance": 5, "summary_he": "כפול", "analysis_he": "",
                  "same_story_as": "141", "repeat_of_sent": False},
                 {"post_id": "143", "importance": 3, "summary_he": "כפול", "analysis_he": "",
                  "same_story_as": "142", "repeat_of_sent": False})           # a chain ends at 141
    sent = []
    summary, _ = run(tmp_path, FakeReader(page), llm, sent)
    assert summary["sent"] == 1 and summary["merged"] == 2 and "כפול" not in sent[0]
    assert sent[0].startswith("🔴 <b>DeItaone</b> · <b>zerohedge</b> · <b>markets</b>: המדד עלה")
    assert sent[0].count("↗") == 3


def test_a_repeat_of_what_was_sent_is_dropped_and_the_sent_list_is_shown(tmp_path):
    llm = picker({"post_id": "151", "importance": 5, "summary_he": "הפד הוריד ריבית", "analysis_he": "",
                  "same_story_as": "", "repeat_of_sent": False})
    run(tmp_path, FakeReader({"tweets": [row("151")], "has_next_page": False}), llm, [])
    llm2 = picker({"post_id": "152", "importance": 5, "summary_he": "שוב הריבית", "analysis_he": "",
                   "same_story_as": "", "repeat_of_sent": True})
    sent = []
    later = datetime(2026, 1, 5, 15, 40, tzinfo=timezone.utc)
    summary, _ = run(tmp_path, FakeReader({"tweets": [row("152")], "has_next_page": False}), llm2, sent,
                     now=later)
    assert summary["repeats"] == 1 and sent == []
    shown = json.loads(llm2.calls[-1])["already_sent"]
    assert shown == [{"author": "NewsDesk", "summary_he": "הפד הוריד ריבית", "minutes_ago": 40}]
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in state["recent"]] == ["151", "152"]          # kept for the explainer


def test_only_the_best_few_go_out_and_the_budget_refills():
    posts = [xnews.to_post(row(str(i), author=f"Desk{i}")) for i in range(5)]

    def pk(post, importance, sources=0):
        return (post, {"importance": importance, "summary_he": "x", "analysis_he": "",
                       "sources": [("Other", "https://x.com/Other/status/9")] * sources})

    day, t0, cap = "2027-01-15", 1_800_000_000, {"max_per_round": 2, "daily_max": 12}
    picks = [pk(posts[0], 3), pk(posts[1], 4), pk(posts[2], 3, 2), pk(posts[3], 5), pk(posts[4], 4, 1)]
    out, budget, held = xnews.ration(picks, {}, t0, day, **cap)
    assert [p.id for p, _ in out] == ["3", "4"] and held == 3     # the 5, then the 4 told by two accounts
    out, budget, held = xnews.ration([pk(posts[0], 4), pk(posts[1], 5)], budget, t0 + 600, day, **cap)
    assert [p.id for p, _ in out] == ["1"] and held == 1          # 10 minutes later: only a 5
    out, budget, _ = xnews.ration([pk(posts[0], 3)], budget, t0 + 600 + 6000, day, **cap)
    assert len(out) == 1 and budget["sent"] == 2                  # 100 minutes on: one regular story
    full = {"tokens": 2, "at": t0, "day": day, "sent": 12}
    assert [p.id for p, _ in xnews.ration([pk(posts[0], 4), pk(posts[1], 5)], full, t0, day, **cap)[0]] == ["1"]
    assert len(xnews.ration([pk(posts[0], 4)], full, t0, "2027-01-16", **cap)[0]) == 1    # a new day
