"""The money-flow map (tascreen/flow.py): made-up stocks and prices."""
from __future__ import annotations

from datetime import date

import numpy as np

from synth import from_knots
import pandas as pd

from tascreen import flow
from tascreen.config import FlowSettings

CFG = FlowSettings()
DAY = date(2026, 9, 4)


def _rising(seed: int, gain: float = 0.6):
    """Closes compounding `gain` % a week (5 sessions), a little noise."""
    rate = gain / 100 / 5
    return from_knots([(0, 50.0)] + [(k, 50.0 * (1 + rate) ** k + (seed % 3) * 0.2) for k in range(20, 320, 20)],
                      seed=seed)


def _flat(seed: int):
    rng = np.random.default_rng(seed)
    return from_knots([(0, 50.0)] + [(k, 50.0 + float(rng.normal(0, 4))) for k in range(10, 320, 10)], seed=seed)


def _universe():
    rows, bars = [], {}
    for n in range(12):
        rows.append({"symbol": f"NASDAQ:UP{n}", "ticker": f"UP{n}", "sector": "Technology Services",
                     "industry": "Packaged Software"})
        bars[f"NASDAQ:UP{n}"] = _rising(n, gain=0.6 + n * 0.02)
    for n in range(12):
        rows.append({"symbol": f"NYSE:FL{n}", "ticker": f"FL{n}", "sector": "Finance", "industry": "Regional Banks"})
        bars[f"NYSE:FL{n}"] = _flat(100 + n)
    for n in range(12):
        rows.append({"symbol": f"NYSE:SL{n}", "ticker": f"SL{n}", "sector": "Utilities", "industry": "Electric Utilities"})
        bars[f"NYSE:SL{n}"] = _rising(200 + n, gain=0.15)
    return pd.DataFrame(rows), bars


def test_a_steady_rise_is_in_the_trend_template_and_a_sideways_drift_is_not():
    up, side = flow.stock_metrics(_rising(1), CFG), flow.stock_metrics(_flat(7), CFG)
    assert up["template"] and up["steady"] and up["ud"] > 1
    assert not side["steady"]
    assert flow.stock_metrics(_rising(1).tail(100), CFG) is None          # too short to judge


def test_only_strong_steady_stocks_of_a_leading_industry_pass():
    stocks, bars = _universe()
    snap = flow.snapshot(stocks, bars.get, _rising(999, gain=0.3), CFG, DAY)
    assert snap["passing"] and all(s.startswith("NASDAQ:UP") for s in snap["passing"])
    assert snap["industries"][0]["name"] == "Packaged Software" and "Packaged Software" in snap["leading_industries"]
    banks = snap["stocks"]["NYSE:FL3"]
    assert not banks["passes"] and "steady" in banks["missing"]
    slow = snap["stocks"]["NYSE:SL3"]                                     # rising, but weaker than most
    assert not slow["passes"] and "rs" in slow["missing"]


def test_the_weekly_map_names_the_groups_and_the_stocks_in_hebrew(tmp_path):
    stocks, bars = _universe()
    snap = flow.snapshot(stocks, bars.get, _rising(999, gain=0.3), CFG, DAY)
    flow.save(tmp_path, snap)
    assert flow.read(tmp_path, DAY.isoformat())["day"] == DAY.isoformat()
    assert flow.read(tmp_path, "2026-09-01") is None                     # nothing on or before that day
    text = flow.weekly_message(snap, None)
    assert "תוכנה" in text and "UP" in text and "Packaged Software" not in text
    assert "-0%" not in text and "לא ייעוץ השקעות" in text


def test_a_stock_of_the_owners_lists_is_judged_without_its_industry():
    stocks, bars = _universe()
    snap = flow.snapshot(stocks, bars.get, None, CFG, DAY)
    assert flow.extra_passes(snap, _rising(5, gain=0.9), CFG)
    assert not flow.extra_passes(snap, _flat(55), CFG)


def test_an_entry_whose_uptrend_broke_leaves_the_setups_list():
    from types import SimpleNamespace

    from tascreen import setups_list

    view = SimpleNamespace(stocks=pd.DataFrame({"symbol": ["NASDAQ:AAA", "NASDAQ:BBB"], "close": [10.0, 10.0]}),
                           day=DAY)
    entries = [{"symbol": "NASDAQ:AAA", "kind": "ma150", "since": "2026-09-01"},
               {"symbol": "NASDAQ:BBB", "kind": "ma150", "since": "2026-09-01"}]
    kept, events = setups_list.review(entries, view, lambda s: None, break_atr=0.5, keep_sessions=10,
                                      trending={"NASDAQ:AAA"})
    assert [e["symbol"] for e in kept] == ["NASDAQ:AAA"]
    assert events == [{"symbol": "NASDAQ:BBB", "kind": "ma150", "what": "removed", "reason": "trend"}]
    assert setups_list.REASONS["trend"]


def test_the_first_map_goes_to_the_group_at_once_then_weekly(tmp_path, monkeypatch):
    import run
    from tascreen import notify

    sent = []
    monkeypatch.setattr(notify, "from_environment", lambda: type("G", (), {"send": lambda self, t, html=False: sent.append(t)})())
    settings = run.load_settings()
    store = type("S", (), {"root": tmp_path})()
    stocks, bars = _universe()
    wednesday, friday = date(2026, 9, 2), date(2026, 9, 4)
    for day in (wednesday, friday):
        flow.save(tmp_path, flow.snapshot(stocks, bars.get, None, CFG, day))
    assert run._flow_weekly(settings, store, wednesday) == "sent (first)" and "המפה הראשונה" in sent[0]
    assert run._flow_weekly(settings, store, wednesday) == "not due"                 # sent once only
    flow.save(tmp_path, flow.snapshot(stocks, bars.get, None, CFG, date(2026, 9, 3)))
    assert run._flow_weekly(settings, store, date(2026, 9, 3)) == "not due"           # Thursday
    assert run._flow_weekly(settings, store, friday) == "sent" and "השבוע שהסתיים" in sent[1]


# ------------------------------------------------- recommended stocks into the TradingView list
def test_the_recommended_stocks_become_list_entries_only_where_the_money_flows():
    from tascreen import setups_list

    live = {"NASDAQ:AAA|ascending_triangle": {"line": 12.5}, "NYSE:OUT|flag": {"line": 40.0}}
    picks = [{"day": "2026-09-04", "symbol": "NASDAQ:BBB", "pattern": "rising_wedge"},
             {"day": "2026-09-03", "symbol": "NASDAQ:OLD", "pattern": "flag"},
             {"day": "2026-09-04", "symbol": "NASDAQ:AAA", "pattern": "flag"}]
    got = setups_list.sent_setups("2026-09-04", picks=picks, live=live, leaders=["NASDAQ:CCC"],
                                  allowed={"NASDAQ:AAA", "NASDAQ:BBB", "NASDAQ:CCC", "NASDAQ:OLD"})
    assert set(got) == {"NASDAQ:AAA", "NASDAQ:BBB", "NASDAQ:CCC"}           # not the other day's, not OUT
    assert got["NASDAQ:AAA"]["kind"] == "alert" and got["NASDAQ:AAA"]["line"] == 12.5
    assert got["NASDAQ:BBB"]["kind"] == "pick" and "pattern" not in got["NASDAQ:BBB"]   # wedges allowed here
    assert got["NASDAQ:CCC"]["kind"] == "flow" and got["NASDAQ:CCC"]["level"] == "none"
    from tascreen.config import SetupsListSettings

    assert setups_list.qualifies({"symbol": "NASDAQ:BBB", **got["NASDAQ:BBB"]}, SetupsListSettings())


def test_a_leader_without_a_level_stays_until_its_uptrend_breaks_or_it_grows_old():
    from types import SimpleNamespace

    from tascreen import setups_list

    bars = _rising(4)
    days = bars["timestamp"].dt.date.astype(str).tolist()
    view = SimpleNamespace(stocks=pd.DataFrame({"symbol": ["NASDAQ:CCC"], "close": [float(bars["close"].iloc[-1])]}),
                           day=bars["timestamp"].iloc[-1].date())
    entry = {"symbol": "NASDAQ:CCC", "kind": "flow", "level": "none", "since": days[-3], "text": "x"}
    kept, events = setups_list.review([entry], view, lambda s: bars, break_atr=0.5, keep_sessions=10,
                                      trending={"NASDAQ:CCC"})
    assert len(kept) == 1 and not events
    old = {**entry, "since": days[-20]}
    assert setups_list.review([old], view, lambda s: bars, break_atr=0.5, keep_sessions=10,
                              trending={"NASDAQ:CCC"})[1][0]["reason"] == "stale"
    assert setups_list.review([entry], view, lambda s: bars, break_atr=0.5, keep_sessions=10,
                              trending=set())[1][0]["reason"] == "trend"


def test_the_first_night_brings_the_last_sessions_recommendations(tmp_path):
    import json

    import run
    from tascreen import alerts
    from tascreen.store import Store

    settings, store = run.load_settings(), Store(tmp_path)
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "picks.json").write_text(json.dumps(
        [{"day": "2026-09-02", "symbol": "NASDAQ:BBB", "pattern": "flag"},
         {"day": "2026-08-20", "symbol": "NASDAQ:OLD", "pattern": "flag"}]), encoding="utf-8")
    alerts.write_sent(store, date(2026, 9, 3), {"live": {"NASDAQ:AAA|flag": {"line": 10.0}}})
    stocks, bars = _universe()
    flow.save(tmp_path, flow.snapshot(stocks, bars.get, None, CFG, DAY))
    first = run._sent_recommendations(settings, store, DAY, None, first=True)
    assert {"NASDAQ:AAA", "NASDAQ:BBB"} <= set(first) and "NASDAQ:OLD" not in first
    assert any(v["kind"] == "flow" for v in first.values())                # the newest map's leaders
    later = run._sent_recommendations(settings, store, date(2026, 9, 3), None)
    assert set(later) == {"NASDAQ:AAA"}                                    # a Thursday: no leaders


def test_a_live_alert_goes_into_the_list_at_once(tmp_path):
    import asyncio
    from contextlib import asynccontextmanager

    import run
    from tascreen import setups_list
    from tascreen.store import Store

    settings, store = run.load_settings(), Store(tmp_path)
    if not settings.setups_list.id:
        import pytest
        pytest.skip("no list id in config.yaml")
    setups_list.write(tmp_path, {"id": settings.setups_list.id, "version": 4, "entries": [
        {"symbol": "NASDAQ:OLD", "kind": "ma150"}], "managed": ["NASDAQ:OLD"]})
    calls = []

    class Session:
        async def call_tool(self, name, args):
            from fakes import result
            calls.append((name, args))
            return result({"success": True})

    class Client:
        @asynccontextmanager
        async def own_list_session(self, list_id, name):
            yield Session()

    found = [{"key": "NASDAQ:NEW|flag", "symbol": "NASDAQ:NEW", "line": 10.0},
             {"key": "NASDAQ:OLD|flag", "symbol": "NASDAQ:OLD", "line": 9.0}]
    assert run._list_alerts(settings, Client(), store, DAY, found) == 1
    assert calls == [(setups_list.ADD, {"watchlist_id": settings.setups_list.id, "symbols": ["NASDAQ:NEW"]})]
    state = setups_list.read(tmp_path)
    assert [e["symbol"] for e in state["entries"]] == ["NASDAQ:OLD", "NASDAQ:NEW"] and "NASDAQ:NEW" in state["managed"]
