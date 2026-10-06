"""The bot's own setups watchlist: the owner's setups (what goes in), the review (what leaves
and why), one evening's refresh, the TradingView sync, the write guard and the private
message. Made-up stocks, prices and volumes only."""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from fakes import result
from synth import frame
from tascreen import setups_list
from tascreen.config import ConfigError, SetupsListSettings
from tascreen.errors import ProviderError
from tascreen.tv.mcp_client import OwnListSession, own_list_edit
from tascreen.web.data import ScanView

CFG = SetupsListSettings(id="111")


def _bars(closes, *, spread=1.0):
    rows, prev = [], closes[0]
    for c in closes:
        rows.append((prev, max(prev, c) + spread, min(prev, c) - spread, c))
        prev = c
    return frame(rows)


D = [str(t.date()) for t in _bars([1.0] * 41)["timestamp"]]      # the sessions' days (synth's calendar)
LAST, DAY = D[-1], date.fromisoformat(D[-1])


def _dm(day):
    return f"{day[8:10]}/{day[5:7]}"


def _analysis(facts, drawings=None):
    return SimpleNamespace(facts={k: {"value": v} for k, v in facts.items()}, drawings=drawings or {})


UPTREND = {"vs_sma150_pct": 8.0, "sma150.direction": "עולה"}
BARS = _bars([100.0 + i * 0.1 for i in range(41)])


# ------------------------------------------------------------------ what goes in
def test_a_hold_of_the_150_day_average_on_enough_volume_goes_in():
    facts = {**UPTREND, "sma150.support.last_result": "החזיק", "sma150.support.sessions_ago": 1,
             "sma150.support.last_volume_ratio": 1.4,
             "sma150.support.state": "ממוצע 150 יום החזיק כתמיכה: המחיר ירד אליו ב-26/02 וקפץ ממנו"}
    got = setups_list.setups_of(BARS, _analysis({**facts, "atr": 2.0, "sma150": 97.0}), CFG)
    assert [(s["kind"], s["since"], s["level"], s["n"], s["strict"]) for s in got] == [("ma150", D[-2], "ma", 150, True)]
    assert (got[0]["watch"], got[0]["atr"]) == (97.0, 2.0)            # what the open is checked against
    # volume that is not weak, or a hold of a few sessions back: only to fill the list to ten
    normal = setups_list.setups_of(BARS, _analysis({**facts, "sma150.support.last_volume_ratio": 1.0}), CFG)
    older = setups_list.setups_of(BARS, _analysis({**facts, "sma150.support.sessions_ago": 4}), CFG)
    assert [s["strict"] for s in normal + older] == [False, False]
    weak = setups_list.setups_of(BARS, _analysis({**facts, "sma150.support.last_volume_ratio": 0.8}), CFG)
    old = setups_list.setups_of(BARS, _analysis({**facts, "sma150.support.sessions_ago": 6}), CFG)
    down = setups_list.setups_of(BARS, _analysis({**facts, "sma150.direction": "יורד"}), CFG)
    assert weak == old == down == []


def test_the_20_day_average_counts_only_as_strong_support_in_a_rise():
    facts = {**UPTREND, "sma20.direction": "עולה", "sma20.support.last_result": "החזיק",
             "sma20.support.sessions_ago": 0, "sma20.support.last_volume_ratio": 1.6,
             "sma20.support.state": "ממוצע 20 יום החזיק כתמיכה: המחיר ירד אליו ב-28/02 וקפץ ממנו",
             "sma20.support.strength": "תמיכה חזקה: ממוצע 20 יום החזיק 2 פעמים ברצף, ובקפיצה האחרונה הנפח לא היה חלש"}
    got = setups_list.setups_of(BARS, _analysis(facts), CFG)
    assert got[0]["kind"] == "ma20" and got[0]["text"].endswith("(תמיכה חזקה)")
    once = {**facts, "sma20.support.strength": "ממוצע 20 יום החזיק כתמיכה"}
    assert setups_list.setups_of(BARS, _analysis(once), CFG) == []


def _pattern(key="flag", breakout=100.0):
    return {"pat_1": {"type": "pattern", "family": "chart", "pattern": key, "status": "breakout",
                      "direction": "bullish", "breakout": breakout, "breakout_day": "2025-02-20",
                      "lines": [{"x1": "2025-02-03", "y1": breakout, "x2": "2025-02-14", "y2": breakout}]}}


RETEST = {**UPTREND, "pat_1.name": "דגל", "pat_1.target": 120.0, "pat_1.sessions_since_breakout": 7,
          "pat_1.state": "המחיר מעל קו הפריצה",
          "pat_1.retest": "המחיר חזר לבדוק את קו הפריצה ב-27/02, החזיק מעליו וקפץ בנפח מעט מעל הממוצע (פי 1.3)",
          "pat_1.retest_sessions_ago": 1, "pat_1.retest_volume_ratio": 1.3}


def test_a_breakout_retest_that_held_goes_in_first_and_follows_its_line():
    facts = {**RETEST, "sma150.support.last_result": "החזיק", "sma150.support.sessions_ago": 0,
             "sma150.support.last_volume_ratio": 2.0, "sma150.support.state": "ממוצע 150 יום החזיק כתמיכה"}
    got = setups_list.setups_of(BARS, _analysis(facts, _pattern()), CFG)
    assert [s["kind"] for s in got] == ["retest", "ma150"]           # the retest is the better one
    best = got[0]
    assert best["pattern"] == "flag" and best["target"] == 120.0 and best["text"].endswith("(תבנית דגל)")
    assert (best["level"], best["line"], best["slope"], best["line_day"]) == ("line", 100.0, 0.0, LAST)


def test_wedges_are_left_out():
    """Owner, 2026-10-05: "without wedges"."""
    for key in ("rising_wedge", "falling_wedge"):
        assert setups_list.setups_of(BARS, _analysis(RETEST, _pattern(key)), CFG) == []


def test_a_fresh_breakout_needs_high_volume_and_to_stay_above_its_line():
    facts = {**UPTREND, "pat_1.name": "דגל", "pat_1.target": 120.0, "pat_1.sessions_since_breakout": 1,
             "pat_1.state": "המחיר מעל קו הפריצה", "pat_1.breakout_volume_ratio": 1.8,
             "pat_1.breakout_volume": "גבוה, פי 1.8 מהממוצע"}
    got = setups_list.setups_of(BARS, _analysis(facts, _pattern()), CFG)
    assert got[0]["kind"] == "breakout" and got[0]["text"] == f"פריצה מתבנית דגל ב-{_dm(D[-2])} בנפח גבוה, פי 1.8 מהממוצע"
    assert got[0]["strict"] and got[0]["watch"] == 100.0             # the flat line, tomorrow
    normal = {**facts, "pat_1.breakout_volume_ratio": 1.2}             # only to fill the list
    assert [s["strict"] for s in setups_list.setups_of(BARS, _analysis(normal, _pattern()), CFG)] == [False]
    thin = {**facts, "pat_1.breakout_volume_ratio": 0.9}
    back = {**facts, "pat_1.state": "המחיר חזר אל תוך התבנית: הפריצה מוטלת בספק"}
    assert setups_list.setups_of(BARS, _analysis(thin, _pattern()), CFG) == []
    assert setups_list.setups_of(BARS, _analysis(back, _pattern()), CFG) == []


def test_the_analyst_finds_a_strong_20_day_support_in_real_bars(tmp_path):
    from test_support import _uptrend_with_pullbacks
    from tascreen.analyst.facts import analyse
    from tascreen.store import Store

    bars = _uptrend_with_pullbacks(8, 1_600_000.0)
    got = setups_list.setups_of(bars, analyse(bars, "TEST:SYN"), CFG)
    assert "ma20" in [s["kind"] for s in got]
    Store(tmp_path).write_bars("NYSE:SYN", bars.assign(symbol="NYSE:SYN"))
    found, counts = setups_list.find_setups(tmp_path, ["NYSE:SYN", "NYSE:NOBARS"], CFG, workers=1)
    assert "NYSE:SYN" in found and counts["analysed"] == 2 and not any(k.startswith("error") for k in counts)


# ------------------------------------------------------------------ what leaves
def _view(stocks, day=DAY):
    return ScanView(day, {}, pd.DataFrame(stocks), pd.DataFrame(columns=["symbol"]))


def _stock(symbol, close, day=DAY):
    return {"symbol": symbol, "close": close, "last_date": day.isoformat()}


def _review(entries, view, bars):
    return setups_list.review(entries, view, lambda s: bars, break_atr=0.5, keep_sessions=10)


FLAT = _bars([100.0] * 41)                 # the averages at 100, a session's range about 2


def _ma_entry(since=D[-3], symbol="NYSE:AAA"):
    return {"symbol": symbol, "kind": "ma20", "level": "ma", "n": 20, "since": since, "volume": 1.5,
            "text": "x", "added": since}


def _line_entry(line=100.0, slope=0.0, target=None, since=D[-3]):
    return {"symbol": "NYSE:AAA", "kind": "retest", "level": "line", "line": line, "slope": slope,
            "line_day": D[-3], "since": since, "target": target, "volume": 1.3, "text": "x", "added": since}


def test_a_close_through_the_average_takes_it_out():
    kept, events = _review([_ma_entry()], _view([_stock("NYSE:AAA", 98.5)]), FLAT)
    assert kept == [] and events[0]["reason"] == "fell_ma"
    kept, events = _review([_ma_entry()], _view([_stock("NYSE:AAA", 99.5)]), FLAT)   # inside the tolerance
    assert len(kept) == 1 and events == []


def test_a_rising_line_is_followed_session_by_session():
    # two sessions on, a line rising 1 a session stands at 102: a close of 100.5 is through it
    assert setups_list.level_now(_line_entry(slope=1.0), FLAT) == 102.0
    kept, events = _review([_line_entry(slope=1.0)], _view([_stock("NYSE:AAA", 100.5)]), FLAT)
    assert events[0]["reason"] == "fell_back"


def test_a_target_reached_or_a_setup_gone_stale_leaves():
    peak = _bars([100.0] * 39 + [106.0, 103.0])
    kept, events = _review([_line_entry(target=105.0)], _view([_stock("NYSE:AAA", 103.0)]), peak)
    assert events[0]["reason"] == "target"
    kept, events = _review([_ma_entry(since=D[-15])], _view([_stock("NYSE:AAA", 100.0)]), FLAT)
    assert events[0]["reason"] == "stale"


def test_a_stock_the_bot_no_longer_follows_leaves_and_a_data_gap_keeps():
    kept, events = _review([_ma_entry()], _view([_stock("NYSE:OTHER", 50.0)]), FLAT)
    assert kept == [] and events[0]["reason"] == "unfollowed"
    gap = _view([_stock("NYSE:AAA", 90.0, day=date.fromisoformat(D[-2]))])
    assert _review([_ma_entry()], gap, FLAT) == ([_ma_entry()], [])


def test_the_oldest_leave_first_when_the_list_is_full():
    entries = [_ma_entry(since=d, symbol=s) for s, d in (("NYSE:A1", D[-9]), ("NYSE:A2", D[-3]), ("NYSE:A3", D[-6]))]
    kept, gone = setups_list.cap(entries, 2)
    assert [e["symbol"] for e in kept] == ["NYSE:A2", "NYSE:A3"] and gone[0]["reason"] == "room"


# ------------------------------------------------------------------ one evening
def test_an_old_list_is_replaced_and_a_breakout_whose_retest_held_is_renewed():
    view = _view([_stock("NYSE:AAA", 100.0), _stock("NYSE:OLD", 50.0), _stock("NYSE:NEW", 20.0)])
    old = {"version": 1, "managed": ["NYSE:GONE"],
           "entries": [{"symbol": "NYSE:OLD", "kind": "setup"}, {"symbol": "NYSE:AAA", "kind": "breakout"}]}
    found = {"NYSE:AAA": {**_line_entry(), "kind": "breakout"}}
    first = setups_list.refresh(old, found, view, lambda s: FLAT, CFG, break_atr=0.5)
    assert first["version"] == setups_list.VERSION
    assert [(e["symbol"], e["what"], e.get("reason")) for e in first["events"]] == [
        ("NYSE:OLD", "removed", "redefined"), ("NYSE:AAA", "added", None)]
    renewed = {"NYSE:AAA": {**_line_entry(since=LAST), "kind": "retest"},
               "NYSE:NEW": _ma_entry(since=LAST, symbol="NYSE:NEW")}
    second = setups_list.refresh(first, renewed, view, lambda s: FLAT, CFG, break_atr=0.5)
    assert sorted((e["symbol"], e["what"]) for e in second["events"]) == [("NYSE:AAA", "renewed"), ("NYSE:NEW", "added")]
    assert second["managed"] == ["NYSE:AAA", "NYSE:GONE", "NYSE:NEW"]
    assert {e["symbol"]: e["kind"] for e in second["entries"]} == {"NYSE:AAA": "retest", "NYSE:NEW": "ma20"}
    assert second["entries"][0]["added"] == LAST                        # the day it first went in


def _found(symbol, since, *, strict, volume=1.5, kind="ma150", watch=95.0):
    return {"kind": kind, "text": f"setup {symbol}", "volume": volume, "since": since, "strict": strict,
            "level": "ma", "n": 150, "watch": watch, "atr": 2.0}


def test_a_short_list_is_filled_to_ten_best_first_and_the_rest_wait_for_the_open():
    view = _view([_stock(f"NYSE:S{i:02d}", 100.0) for i in range(16)])
    found = {f"NYSE:S{i:02d}": _found(f"NYSE:S{i:02d}", D[-1], strict=i < 3) for i in range(3)}
    found.update({f"NYSE:S{i:02d}": _found(f"NYSE:S{i:02d}", D[-1 - i % 5], strict=False, volume=0.9 + i / 100)
                  for i in range(3, 16)})
    state = setups_list.refresh({"version": setups_list.VERSION}, found, view, lambda s: FLAT, CFG, break_atr=0.5)
    assert len(state["entries"]) == 10
    filled = [e for e in state["entries"] if e.get("filled")]
    assert len(filled) == 7 and all(not e["strict"] for e in filled)
    assert [e["what"] for e in state["events"]].count("filled") == 7
    # the newest of the filling ones went first; the others wait in the pool
    assert max(e["since"] for e in filled) == D[-1] and len(state["pool"]) == 6
    assert all(c["symbol"] not in {e["symbol"] for e in state["entries"]} for c in state["pool"])
    full = setups_list.refresh({"version": setups_list.VERSION}, {k: {**v, "strict": True} for k, v in found.items()},
                               view, lambda s: FLAT, CFG, break_atr=0.5)
    assert len(full["entries"]) == 16 and not any(e.get("filled") for e in full["entries"])


def test_the_open_takes_out_what_opened_under_its_level_and_refills_from_the_pool():
    entries = [{**_found("NYSE:A", D[-1], strict=True), "symbol": "NYSE:A"},
               {**_found("NYSE:B", D[-1], strict=True), "symbol": "NYSE:B"}]
    pool = [{**_found("NYSE:C", D[-2], strict=False, watch=50.0), "symbol": "NYSE:C"},
            {**_found("NYSE:D", D[-2], strict=False, watch=50.0), "symbol": "NYSE:D"}]
    state = {"version": setups_list.VERSION, "entries": entries, "pool": pool, "managed": ["NYSE:A", "NYSE:B"]}
    cfg = dataclasses.replace(CFG, min_size=2)
    # A opens at 93.5, more than half an ATR (2) under its 95: out; C is above its 50: in; D has no price
    got = setups_list.open_review(state, {"NYSE:A": 93.5, "NYSE:B": 94.5, "NYSE:C": 51.0}, cfg,
                                  break_atr=0.5, day=LAST)
    assert [e["symbol"] for e in got["entries"]] == ["NYSE:B", "NYSE:C"] and got["open_day"] == LAST
    assert [(e["symbol"], e["what"], e.get("reason")) for e in got["events"]] == [
        ("NYSE:A", "removed", "opened_below"), ("NYSE:C", "filled", None)]
    assert [c["symbol"] for c in got["pool"]] == ["NYSE:D"] and "NYSE:C" in got["managed"]
    text = setups_list.message(got, at_open=True)
    assert text.startswith("📋 <b>רשימת הסטאפים בטריידינגוויו עודכנה בפתיחת המסחר</b>")
    assert "• A: נפתחה מתחת לרמה שלה: התמיכה נשברה בפתיחה" in text


def test_the_message_tells_what_changed():
    state = {"entries": [{"symbol": "NYSE:AAA", "kind": "retest"}, {"symbol": "NYSE:BBB", "kind": "ma150"}],
             "events": [{"symbol": "NYSE:BBB", "kind": "ma150", "what": "added", "text": "ממוצע 150 יום החזיק כתמיכה"},
                        {"symbol": "NYSE:AAA", "kind": "retest", "what": "renewed", "text": "החזיק מעל קו הפריצה"},
                        {"symbol": "NYSE:CCC", "kind": "ma20", "what": "removed", "reason": "fell_ma"}]}
    text = setups_list.message(state)
    assert "• BBB (ממוצע 150): ממוצע 150 יום החזיק כתמיכה" in text
    assert "<b>התחדשו:</b>" in text and "• CCC: נסגרה מתחת לממוצע: התמיכה נשברה" in text
    assert text.endswith("ברשימה עכשיו: 2 מניות (1 בדיקה אחרי פריצה, 1 ממוצע 150).")
    assert setups_list.message({"entries": [], "events": []}) is None


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
    with pytest.raises(ConfigError):
        SetupsListSettings(min_bounce_volume=0.1)
    assert SetupsListSettings(exclude_patterns=["rising_wedge"]).exclude_patterns == ("rising_wedge",)
