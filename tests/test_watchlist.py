"""The owner's own watchlist: read from TradingView's answer, named in a text, a move worth
an alert, the private chat, and X news about it. Synthetic symbols and prices only."""
from __future__ import annotations

import json

from tascreen import notify, watchlist


def test_the_list_is_read_without_its_section_headers():
    payload = {"watchlist": {"name": "mine", "symbols": ["NASDAQ:AAA", "###SECTION 1", "AMEX:BBB", 5, "CCC"]}}
    assert watchlist.symbols_of(payload) == ("mine", ["NASDAQ:AAA", "AMEX:BBB"])
    assert watchlist.tickers(["NASDAQ:AAA", "AMEX:BBB"]) == ["AAA", "BBB"]


def test_a_text_names_a_ticker_as_a_word_or_a_cashtag():
    names = ["AAA", "BB"]
    assert watchlist.mentions("$AAA beats estimates", names) == ["AAA"]
    assert watchlist.mentions("AAAB and aaa and BBC", names) == []
    assert watchlist.mentions("חברת BB, כמו AAA.", names) == ["AAA", "BB"]


def test_a_move_is_told_at_each_further_step_once():
    closes = {"NASDAQ:AAA": 100.0, "NASDAQ:BBB": 50.0}
    moves = watchlist.new_moves({"NASDAQ:AAA": 104.5, "NASDAQ:BBB": 49.0}, closes, {}, 4)
    assert [(m["symbol"], m["side"], m["level"]) for m in moves] == [("NASDAQ:AAA", "up", 4)]
    sent = {"NASDAQ:AAA": {"up": 4}}
    assert watchlist.new_moves({"NASDAQ:AAA": 106.0}, closes, sent, 4) == []
    again = watchlist.new_moves({"NASDAQ:AAA": 108.2}, closes, sent, 4)
    assert again[0]["level"] == 8
    down = watchlist.new_moves({"NASDAQ:BBB": 47.9}, closes, {}, 4)
    text = watchlist.move_message(down)
    assert text.startswith("📌 <b>תנועה חריגה ברשימה שלך</b>") and "BBB ▼ -4.2%" in text


def test_the_watchlist_goes_to_the_owners_private_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "x" * 30)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("TELEGRAM_GROUP_ID", "-555")
    assert notify.from_environment().chat_id == "-555" and notify.owner_from_environment().chat_id == "42"


def test_the_kept_list_is_used_only_for_its_id(tmp_path):
    import dataclasses

    import run
    from tascreen.config import Settings, WatchlistSettings

    settings = dataclasses.replace(Settings(root=tmp_path), watchlist=WatchlistSettings(id="123"))
    watchlist.write(settings.data_dir, {"id": "123", "symbols": ["NASDAQ:AAA", "AMEX:BBB"], "day": "2026-01-05"})
    assert run._saved_holdings(settings) == ["AAA", "BBB"]
    other = dataclasses.replace(settings, watchlist=WatchlistSettings(id="999"))
    assert run._saved_holdings(other) == []


def test_news_about_a_holding_the_group_did_not_get_goes_to_the_private_chat(tmp_path):
    from test_xnews import FakeReader, picker, row, run as run_news

    page = {"tweets": [row("1", text="$AAA raises guidance"), row("2", author="Desk2", text="Market wrap")],
            "has_next_page": False}
    llm = picker({"post_id": "1", "importance": 3, "summary_he": "AAA העלתה תחזית", "analysis_he": ""},
                 {"post_id": "2", "importance": 4, "summary_he": "סיכום השוק", "analysis_he": ""})
    group, private = [], []
    summary, _ = run_news(tmp_path, FakeReader(page), llm, group, min_importance=4, holdings=["AAA"],
                          send_private=private.append)
    assert summary["sent"] == 1 and "AAA" not in group[0]
    assert len(private) == 1 and private[0].startswith(watchlist.NEWS_HEAD) and "AAA העלתה תחזית" in private[0]
    assert '"investor_holdings"' in llm.calls[0] and summary["holding_news"] == 1


def test_a_holding_crossing_and_move_go_privately_once(tmp_path, monkeypatch):
    import run
    from scan_setup import LIVE_PRICE
    from test_alerts import _live_setup

    settings, store, view, session = _live_setup(tmp_path)
    monkeypatch.setattr(run, "_news", lambda settings, symbols, client=None: {})
    monkeypatch.setattr("tascreen.tv.mcp_client.push_token_file", lambda path, why: None)

    class Owner:
        def __init__(self):
            self.sent = []

        def send(self, text, html=False):
            self.sent.append(text)

    owner, sent = Owner(), {}
    got = {"prices": {"NASDAQ:DB": LIVE_PRICE, "NASDAQ:ZZZ": 1.0}, "closes": {"NASDAQ:DB": LIVE_PRICE / 1.05}}
    summary = {"holding_alerts": 0}
    run._holding_alerts(settings, owner, None, store, view, got, ["NASDAQ:DB"], session, sent, summary)
    assert len(owner.sent) == 2 and owner.sent[0].startswith(watchlist.CROSSING_HEAD)
    assert owner.sent[1].startswith("📌 <b>תנועה חריגה") and summary["holding_alerts"] == 2
    assert list(sent["holding_live"]) == ["NASDAQ:DB|double_bottom"] and sent["holding_moves"]["NASDAQ:DB"] == {"up": 4}
    run._holding_alerts(settings, owner, None, store, view, got, ["NASDAQ:DB"], session, sent, summary)
    assert len(owner.sent) == 2                                          # each told once
