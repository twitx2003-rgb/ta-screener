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
                             daily_read_cap=kw.pop("daily_read_cap", 1000), **kw)
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
