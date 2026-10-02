"""The bot's own setups watchlist: who goes in, who leaves and why, the TradingView sync, the
write guard and the private message. Made-up stocks and prices only."""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import date

import pandas as pd
import pytest

from tascreen import setups_list
from tascreen.config import ConfigError, SetupsListSettings
from tascreen.errors import ProviderError
from tascreen.tv.mcp_client import OwnListSession, own_list_edit
from tascreen.web.data import ScanView
from fakes import result
from test_alerts import _det

DAY = date(2026, 3, 20)
CFG = SetupsListSettings(id="111", max_size=40, max_gap_pct=8, setup_sessions=15, breakout_sessions=10)


def _view(stocks, detections, day=DAY) -> ScanView:
    return ScanView(day, {}, pd.DataFrame(stocks), pd.DataFrame(detections))


def _stock(symbol, close, day=DAY):
    return {"symbol": symbol, "close": close, "rel_volume": 1.0, "last_date": day.isoformat()}


def _bars(highs, start="2026-03-02"):
    days = pd.bdate_range(start, periods=len(highs))
    return pd.DataFrame({"timestamp": days, "symbol": "X", "open": highs, "high": highs,
                         "low": highs, "close": highs, "volume": 1.0})


def _pick(symbol, pattern, kind, day=DAY):
    return {"day": day.isoformat(), "symbol": symbol, "pattern": pattern, "kind": kind}


# ------------------------------------------------------------------ in
def test_the_sessions_picks_go_in_as_a_setup_or_a_breakout():
    view = _view([_stock("NYSE:AAA", 101.0), _stock("NYSE:BBB", 48.0)],
                 [_det("NYSE:AAA", "double_bottom", "breakout", "bullish"),
                  _det("NYSE:BBB", "flag", "forming", "bullish", trigger=50.0)])
    picks = [_pick("NYSE:AAA", "double_bottom", "breakout"), _pick("NYSE:BBB", "flag", "verge"),
             _pick("NYSE:CCC", "flag", "verge", day=date(2026, 3, 19)),     # another session's pick
             _pick("NYSE:DDD", "flag", "verge")]                             # not in the scan
    got = setups_list.new_entries(picks, view, lambda s: None, set())
    assert [(e["symbol"], e["kind"], e["line"]) for e in got] == [("NYSE:AAA", "breakout", 100.0),
                                                                  ("NYSE:BBB", "setup", 50.0)]
    assert got[0]["breakout_day"] == "2026-03-20" and got[0]["target"] == 110.0
    assert setups_list.new_entries(picks, view, lambda s: None, {"NYSE:AAA", "NYSE:BBB"}) == []


# ------------------------------------------------------------------ out
def _setup(symbol="NYSE:BBB", added="2026-03-18", line=50.0, **kw):
    return {"symbol": symbol, "pattern": "flag", "name": "דגל", "kind": "setup", "added": added,
            "line": line, "target": 60.0, "breakout_day": "", "missing": 0, **kw}


def _breakout(symbol="NYSE:AAA", line=100.0, target=110.0, day="2026-03-18"):
    return {"symbol": symbol, "pattern": "double_bottom", "name": "תחתית כפולה", "kind": "breakout",
            "added": day, "line": line, "target": target, "breakout_day": day, "missing": 0}


def _review(entries, view, bars=None):
    return setups_list.review(entries, view, lambda s: bars, max_gap_pct=8, setup_sessions=15,
                              breakout_sessions=10)


def test_a_breakout_that_closes_back_below_its_line_leaves():
    kept, events = _review([_breakout()], _view([_stock("NYSE:AAA", 99.0)], [_det("NYSE:ZZZ", "flag", "forming", "bullish")]))
    assert kept == [] and events[0]["reason"] == "fell_back"


def test_a_breakout_that_reached_its_target_leaves_as_a_success():
    bars = _bars([100.0] * 13 + [111.0, 105.0])          # a high at the target after the breakout day
    kept, events = _review([_breakout()], _view([_stock("NYSE:AAA", 105.0)], [_det("NYSE:ZZZ", "flag", "forming", "bullish")]), bars)
    assert kept == [] and events[0]["reason"] == "target"


def test_a_breakout_is_fresh_for_breakout_sessions_only():
    view = _view([_stock("NYSE:AAA", 104.0)], [_det("NYSE:ZZZ", "flag", "forming", "bullish")])
    fresh = _bars([104.0] * 15)                          # 2026-03-02..20: two sessions after the 18th
    assert _review([_breakout()], view, fresh)[0] != []
    kept, events = _review([_breakout(day="2026-03-04")], view, fresh)   # 12 sessions after
    assert kept == [] and events[0]["reason"] == "stale"


def test_a_setup_that_broke_out_stays_as_a_breakout():
    view = _view([_stock("NYSE:BBB", 51.0)],
                 [_det("NYSE:BBB", "flag", "breakout", "bullish", breakout=50.0, target=60.0)])
    kept, events = _review([_setup()], view)
    assert kept[0]["kind"] == "breakout" and kept[0]["breakout_day"] == "2026-03-20"
    assert [e["what"] for e in events] == ["broke_out"]


def test_a_setup_that_drifts_away_or_vanishes_or_waits_too_long_leaves():
    far = _view([_stock("NYSE:BBB", 45.0)], [_det("NYSE:BBB", "flag", "forming", "bullish", trigger=50.0)])
    assert _review([_setup()], far)[1][0]["reason"] == "drifted"         # 11% below the line
    gone = _view([_stock("NYSE:BBB", 49.0)], [_det("NYSE:ZZZ", "flag", "forming", "bullish")])
    kept, events = _review([_setup()], gone)
    assert kept[0]["missing"] == 1 and events == []                      # one session's grace
    assert _review(kept, gone)[1][0]["reason"] == "vanished"
    near = _view([_stock("NYSE:BBB", 49.0)], [_det("NYSE:BBB", "flag", "forming", "bullish", trigger=50.0)])
    assert _review([_setup(added="2026-02-20")], near, _bars([49.0] * 30, start="2026-02-09"))[1][0]["reason"] \
        == "no_breakout"


def test_an_entry_without_the_sessions_close_is_kept_as_is():
    old = _view([_stock("NYSE:AAA", 90.0, day=date(2026, 3, 19))], [_det("NYSE:ZZZ", "flag", "forming", "bullish")])
    assert _review([_breakout()], old) == ([_breakout()], [])


def test_the_oldest_leave_first_when_the_list_is_full():
    entries = [_setup("NYSE:A1", added="2026-03-10"), _setup("NYSE:A2", added="2026-03-12"),
               _setup("NYSE:A3", added="2026-03-11")]
    kept, gone = setups_list.cap(entries, 2)
    assert [e["symbol"] for e in kept] == ["NYSE:A2", "NYSE:A3"] and gone[0]["reason"] == "room"


def test_refresh_keeps_what_the_bot_ever_added():
    view = _view([_stock("NYSE:BBB", 48.0)], [_det("NYSE:BBB", "flag", "forming", "bullish", trigger=50.0)])
    state = setups_list.refresh({"managed": ["NYSE:OLD"]}, [_pick("NYSE:BBB", "flag", "verge")], view,
                                lambda s: None, CFG)
    assert state["managed"] == ["NYSE:BBB", "NYSE:OLD"] and state["day"] == "2026-03-20"
    assert state["events"] == [{"symbol": "NYSE:BBB", "name": "שם flag", "what": "added", "reason": "setup"}]


# ------------------------------------------------------------------ TradingView
class FakeSession:
    def __init__(self, symbols):
        self.symbols, self.calls = list(symbols), []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return result({"watchlist": {"id": 111, "name": setups_list.NAME, "symbols": self.symbols}})


def test_sync_adds_what_is_missing_and_removes_only_its_own():
    session = FakeSession(["NYSE:KEEP", "NYSE:OWNER", "NYSE:GONE"])
    counts = asyncio.run(setups_list.sync(session, "111", ["NYSE:KEEP", "NYSE:NEW"],
                                          ["NYSE:KEEP", "NYSE:GONE"], delays=()))
    assert counts == {"added": 1, "removed": 1}
    assert session.calls[1] == (setups_list.ADD, {"watchlist_id": "111", "symbols": ["NYSE:NEW"]})
    assert session.calls[2] == (setups_list.REMOVE, {"watchlist_id": "111", "symbols": ["NYSE:GONE"]})


def test_only_the_bots_own_list_can_be_edited():
    assert own_list_edit("mcp-watchlist-add-to-watchlist", {"watchlist_id": "111"}, "111", "n")
    assert own_list_edit("mcp-watchlist-remove-from-watchlist", {"watchlist_id": 111}, "111", "n")
    assert not own_list_edit("mcp-watchlist-add-to-watchlist", {"watchlist_id": "222"}, "111", "n")
    assert not own_list_edit("mcp-watchlist-add-to-watchlist", {"watchlist_id": ""}, "", "n")
    assert not own_list_edit("mcp-watchlist-delete-watchlist", {"watchlist_id": "111"}, "111", "n")
    assert not own_list_edit("mcp-watchlist-update-watchlist", {"watchlist_id": "111"}, "111", "n")
    assert not own_list_edit("mcp-tv-create-alert", {}, "111", "n")
    assert own_list_edit("mcp-watchlist-create-watchlist", {"name": "n"}, "", "n")
    assert not own_list_edit("mcp-watchlist-create-watchlist", {"name": "n"}, "111", "n")
    assert not own_list_edit("mcp-watchlist-create-watchlist", {"name": "other"}, "", "n")

    session = OwnListSession(FakeSession([]), "111", "n")
    with pytest.raises(ProviderError):
        asyncio.run(session.call_tool("mcp-watchlist-add-to-watchlist", {"watchlist_id": "222", "symbols": []}))
    with pytest.raises(ProviderError):
        asyncio.run(session.call_tool("mcp-watchlist-delete-watchlist", {"watchlist_id": "111"}))
    asyncio.run(session.call_tool("mcp-watchlist-get-watchlist", {"watchlist_id": "999"}))   # reads pass


def test_the_owners_own_list_is_never_the_bots(tmp_path):
    import run
    from tascreen.config import Settings, WatchlistSettings
    from tascreen.store import Store

    settings = dataclasses.replace(Settings(root=tmp_path), watchlist=WatchlistSettings(id="111"),
                                   setups_list=SetupsListSettings(id="111"))
    got = run._setups_list(settings, Store(settings.data_dir), DAY)
    assert got["status"].startswith("refused")


def test_settings_are_checked():
    with pytest.raises(ConfigError):
        SetupsListSettings(id="abc")
    with pytest.raises(ConfigError):
        SetupsListSettings(max_size=0)


# ------------------------------------------------------------------ the message
def test_the_message_tells_what_changed():
    state = {"entries": [_setup(), _breakout()], "events": [
        {"symbol": "NYSE:BBB", "name": "דגל", "what": "added", "reason": "setup"},
        {"symbol": "NYSE:AAA", "name": "תחתית כפולה", "what": "broke_out", "reason": ""},
        {"symbol": "NYSE:CCC", "name": "x", "what": "removed", "reason": "fell_back"}]}
    text = setups_list.message(state)
    assert "• BBB: דגל (סטאפ לפני פריצה)" in text and "• CCC: נסגרה מתחת לקו הפריצה" in text
    assert text.endswith("ברשימה עכשיו: 2 מניות (1 סטאפים, 1 אחרי פריצה).")
    assert setups_list.message({"entries": [], "events": []}) is None
