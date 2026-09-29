"""Big moves before the open: fresh vs stale screener rows, the slots, the message, and
the watch that sends the slots before the session. Synthetic values only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tascreen import premarket
from tascreen.config import AlertsSettings, UniverseSettings
from tascreen.errors import ConfigError

NY = "America/New_York"


def row(symbol="NASDAQ:AAA", close=100.0, change_abs=5.0, price=105.0, pct=5.0, volume=90000.0, **kw):
    return {"symbol": symbol, "close": close, "premarket_change_abs": change_abs, "premarket_close": price,
            "premarket_change": pct, "premarket_volume": volume, "description": "Company A", **kw}


def test_only_todays_figures_are_used():
    assert premarket.mover(row(), "t", ["OTC"])["price"] == 105.0
    # yesterday's pre-market: its base was the close before `close`
    assert premarket.mover(row(close=103.0), "t", ["OTC"]) is None
    assert premarket.mover(row(symbol="OTC:BBB"), "t", ["OTC"]) is None
    assert premarket.mover(row(premarket_close=None, price=None), "t", ["OTC"]) is None


def test_the_slots():
    monday = lambda h, m: datetime(2026, 1, 5, h, m, tzinfo=timezone.utc) + timedelta(hours=5)  # EST
    assert premarket.slot_due(monday(7, 20), NY, {}) == (None, "between slots")
    assert premarket.slot_due(monday(7, 35), NY, {})[1] == "07:30"
    assert premarket.slot_due(monday(7, 35), NY, {"07:30": "x"}) == (None, "already sent")
    assert premarket.slot_due(monday(8, 20), NY, {}) == (None, "between slots")   # 07:30 + 40 min passed
    assert premarket.slot_due(monday(9, 20), NY, {})[1] == "09:15"
    assert premarket.slot_due(monday(9, 31), NY, {})[0] is None                    # open
    saturday = datetime(2026, 1, 10, 13, 0, tzinfo=timezone.utc)
    assert premarket.slot_due(saturday, NY, {})[0] is None


def test_the_screener_asks_for_the_right_side():
    up = premarket.arguments(UniverseSettings(), up=True, min_pct=4, min_volume=50000, limit=40)
    down = premarket.arguments(UniverseSettings(), up=False, min_pct=4, min_volume=50000, limit=40)
    assert up["filters"]["premarket_change"] == [4, None] and up["sort_order"] == "desc"
    assert down["filters"]["premarket_change"] == [None, -4] and down["sort_order"] == "asc"
    assert "premarket_close" in up["columns"]


def test_the_message():
    movers = {"up": [premarket.mover(row(), "t", [])], "down": [
        premarket.mover(row("NYSE:CCC", 50.0, -4.0, 46.0, -8.0, description="<C>"), "t", [])], "stale": 0}
    crossings = [{"symbol": "NASDAQ:AAA", "name": "משולש עולה", "line": 104.0, "price": 105.0}]
    text = premarket.message("08:30", movers, crossings, 4)
    assert text.startswith("🌅 <b>לפני הפתיחה</b> (08:30 בניו יורק)")
    assert "קו 104.00 · עכשיו 105.00" in text and "+5.0% · 105.00" in text and "-8.0%" in text
    assert "&lt;C&gt;" in text
    quiet = premarket.message("07:30", {"up": [], "down": [], "stale": 3}, [], 4)
    assert "אין מניות שזזות יותר מ-4%" in quiet


def test_the_watch_sends_each_slot_then_hands_over_to_the_session(monkeypatch):
    import run

    clock = {"t": datetime(2026, 1, 5, 12, 25, tzinfo=timezone.utc)}           # 07:25 New York
    opens = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    calls = []
    monkeypatch.setattr(run, "ci_premarket", lambda s: calls.append(clock["t"]) or 0)

    def sleep(seconds):
        clock["t"] += timedelta(seconds=seconds)

    statuses = run.premarket_until_open(run.load_settings(), opens, sleep=sleep, now=lambda: clock["t"])
    assert statuses == ["ok"] * 4                       # at the start, then 07:30, 08:30, 09:15
    assert [t.strftime("%H:%M") for t in calls[1:]] == ["12:30", "13:30", "14:15"]


def test_a_failed_report_is_tried_again_within_its_slot(monkeypatch):
    import run
    from tascreen.errors import ProviderError

    clock = {"t": datetime(2026, 1, 5, 12, 30, 5, tzinfo=timezone.utc)}        # 07:30 New York
    opens = datetime(2026, 1, 5, 13, 0, tzinfo=timezone.utc)
    calls = []

    def report(settings):
        calls.append(clock["t"].strftime("%H:%M"))
        if len(calls) == 1:
            raise ProviderError("TradingView: rate limited")
        return 0

    def sleep(seconds):
        clock["t"] += timedelta(seconds=seconds)

    monkeypatch.setattr(run, "ci_premarket", report)
    statuses = run.premarket_until_open(run.load_settings(), opens, sleep=sleep, now=lambda: clock["t"])
    assert statuses[:2] == ["ProviderError", "ok"] and calls[:2] == ["12:30", "12:35"]


def test_the_settings_are_checked():
    with pytest.raises(ConfigError):
        AlertsSettings(premarket_min_pct=0)
