"""The website with live quotes (made-up prices): live prices, crossings, staleness."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from synth import from_knots
from tascreen.config import Settings
from tascreen.market_hours import next_sessions
from tascreen.patterns.rules import load_rules
from tascreen.scan import run_scan
from tascreen.store import Store
from tascreen.web import fmt
from tascreen.web.app import HOST, create_app
from test_chart_patterns import HS_TOP
from test_scan import _universe

RULES = load_rules()
# A double bottom still forming on the last bar (the same shape as the chart test's,
# moved so that it ends on the same session as HS_TOP).
FORMING_DB = [(0, 130), (133, 100), (148, 112), (163, 100.5), (168, 105)]
LIVE_PRICE = 113.0        # above the middle peak (~112.3): crossing upward


def _setup(tmp_path):
    settings = Settings(root=tmp_path)
    store = Store(settings.data_dir)
    hs, db = from_knots(HS_TOP), from_knots(FORMING_DB)
    assert hs["timestamp"].iloc[-1] == db["timestamp"].iloc[-1]
    store.write_bars("NYSE:HS", hs.assign(symbol="NYSE:HS"))
    store.write_bars("NASDAQ:DB", db.assign(symbol="NASDAQ:DB"))
    day = hs["timestamp"].iloc[-1].date()
    run_scan(store, _universe(["NYSE:HS", "NASDAQ:DB"]), date(2025, 9, 1), RULES, day,
             progress=lambda m: None)
    return settings, store, day


def _quotes(store, session, fetched_at, db_price=LIVE_PRICE):
    frame = pd.DataFrame({"symbol": ["NYSE:HS", "NASDAQ:DB"], "price": [50.0, db_price],
                          "change_pct": [-1.0, 2.5], "change_abs": [-0.5, 2.7],
                          "volume": [1e6, 2e6], "rel_volume": [1.0, 1.4], "market_cap": [3e9, 2e9]})
    store.write_quotes(frame, {"fetched_at": fetched_at.isoformat(timespec="seconds"),
                               "session": session.isoformat() if session else None,
                               "rows": 2, "calls": 3, "complete": True, "missing": 0, "seconds": 1.0})


def _client(settings):
    return TestClient(create_app(settings, rules=RULES), base_url=f"http://{HOST}")


def _ok(response):
    assert response.status_code == 200, response.text[:400]
    assert fmt.page_problems(response.text) == []
    return response.text


def test_the_scan_has_a_forming_double_bottom_with_a_trigger(tmp_path):
    settings, store, day = _setup(tmp_path)
    _, patterns, _ = store.read_scan(day)
    db = patterns[(patterns.symbol == "NASDAQ:DB") & (patterns.pattern == "double_bottom")].iloc[0]
    assert db.status == "forming" and 111 < db.trigger_up < LIVE_PRICE


def test_fresh_quotes_replace_prices_and_show_crossings(tmp_path):
    settings, store, day = _setup(tmp_path)
    _quotes(store, next_sessions(day, 1)[0], datetime.now(timezone.utc))
    client = _client(settings)

    html = _ok(client.get("/screener"))
    assert "מחירים חיים" in html and "live-dot" in html and "חוצה עכשיו" in html

    data = client.get("/api/scan?live=cross").json()
    assert data["live"]["active"] and data["live"]["crossings"] == 1
    [row] = data["results"]
    assert row["symbol"] == "NASDAQ:DB" and row["live"] and row["close"] == LIVE_PRICE
    assert row["last_close"] != LIVE_PRICE and row["change_1d_pct"] == 2.5
    assert row["crossings"][0]["direction"] == "bullish"

    page = _ok(client.get("/symbol/NASDAQ:DB"))
    assert "חוצה עכשיו את קו הפריצה" in page and "קו הפריצה ליום המסחר הבא" in page
    assert client.get("/api/live").json()["active"] is True


def test_a_price_short_of_the_level_is_not_a_crossing(tmp_path):
    settings, store, day = _setup(tmp_path)
    _quotes(store, next_sessions(day, 1)[0], datetime.now(timezone.utc), db_price=108.0)
    data = _client(settings).get("/api/scan").json()
    assert data["live"]["active"] and data["live"]["crossings"] == 0


def test_stale_quotes_are_flagged_and_not_used(tmp_path):
    settings, store, day = _setup(tmp_path)
    _quotes(store, next_sessions(day, 1)[0], datetime.now(timezone.utc) - timedelta(hours=2))
    client = _client(settings)
    html = _ok(client.get("/screener"))
    assert "המחירים החיים לא התעדכנו" in html and "חוצה עכשיו" not in html
    rows = client.get("/api/scan").json()["results"]
    assert not any(r["live"] for r in rows)


@pytest.mark.parametrize("session_of", ["scan_day", "closed"])
def test_quotes_that_the_scan_already_covers_are_ignored(tmp_path, session_of):
    settings, store, day = _setup(tmp_path)
    _quotes(store, day if session_of == "scan_day" else None, datetime.now(timezone.utc))
    client = _client(settings)
    html = _ok(client.get("/screener"))
    assert "מחירים חיים" not in html
    assert client.get("/api/live").json()["active"] is False
    _ok(client.get("/status"))


def test_live_filter_without_live_quotes_explains_why_it_is_empty(tmp_path):
    settings, _, _ = _setup(tmp_path)
    html = _ok(_client(settings).get("/screener?live=cross"))
    assert "אין כרגע מחירים חיים" in html


def test_status_page_shows_the_quotes(tmp_path):
    settings, store, day = _setup(tmp_path)
    _quotes(store, next_sessions(day, 1)[0], datetime.now(timezone.utc))
    html = _ok(_client(settings).get("/status"))
    assert "מחירים חיים" in html and "מתעדכן" in html
