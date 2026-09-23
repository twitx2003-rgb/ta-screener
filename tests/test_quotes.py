"""Live quotes: the session window, the screener fetch, crossings, the deadline. Synthetic values."""
from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from fakes import FakeClient, FakeOhlcv, FakeScreener, stock_row
from synth import from_knots
from tascreen.bars import BarsJob, update_all
from tascreen.config import BarsSettings, ConfigError, LiveSettings, MarketSettings, UniverseSettings
from tascreen.market_hours import live_session, next_open
from tascreen.patterns.chart import detect_chart
from tascreen.patterns.rules import load_rules
from tascreen.quotes import crossings, fetch_quotes
from tascreen.store import Store
from test_chart_patterns import ASC_TRIANGLE, DOUBLE_BOTTOM

NY = "America/New_York"
RULES = load_rules()


def utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# ------------------------------------------------------------ session window
@pytest.mark.parametrize("now, expected", [
    (utc(2026, 9, 23, 13, 29), None),                  # 09:29 New York
    (utc(2026, 9, 23, 13, 30), date(2026, 9, 23)),     # the open
    (utc(2026, 9, 23, 20, 19), date(2026, 9, 23)),     # 16:19: delayed quotes still arriving
    (utc(2026, 9, 23, 20, 20), None),                  # 16:20: done
    (utc(2026, 9, 26, 15, 0), None),                   # Saturday
    (utc(2026, 11, 26, 15, 0), None),                  # Thanksgiving
    (utc(2026, 11, 27, 18, 19), date(2026, 11, 27)),   # half-day: 13:19
    (utc(2026, 11, 27, 18, 20), None),                 # 13:20 after the 13:00 close
])
def test_live_session_window(now, expected):
    assert live_session(now, market_tz=NY, after_close_minutes=20) == expected


def test_next_open_skips_weekends_and_holidays():
    assert next_open(utc(2026, 9, 25, 21, 0), market_tz=NY) == utc(2026, 9, 28, 13, 30)
    assert next_open(utc(2026, 11, 25, 22, 0), market_tz=NY) == utc(2026, 11, 27, 14, 30)  # EST


# --------------------------------------------------------------------- fetch
def test_quotes_come_from_the_band_fetch_without_otc_or_preferred():
    rows = [stock_row(i, (1.01 + 0.37 * i) * 1e9) for i in range(30)]
    rows.append(stock_row(98, 3e9, "OTC"))
    rows.append(stock_row(99, 4e9, subtype="preferred"))
    cfg = UniverseSettings(band_edges=(1e9, 2e9, 5e9), row_cap=10)
    frame, summary = asyncio.run(fetch_quotes(FakeScreener(rows, cap=10), cfg, delays=()))
    assert len(frame) == 30 and summary["complete"] and summary["missing"] == 0
    assert summary["calls"] >= 4                       # count call + bands
    row = frame.set_index("symbol").loc["NASDAQ:S0003"]
    assert row["price"] == 13.0 and row["change_pct"] == 1.5 and row["rel_volume"] == 1.2


def test_a_row_without_the_change_column_fails_loudly():
    rows = [stock_row(0, 1.5e9)]
    del rows[0]["change"]
    cfg = UniverseSettings(band_edges=(1e9, 2e9), row_cap=10)
    with pytest.raises(Exception, match="change"):
        asyncio.run(fetch_quotes(FakeScreener(rows), cfg, delays=()))


def test_quotes_round_trip_through_the_store(tmp_path):
    store = Store(tmp_path)
    frame = pd.DataFrame({"symbol": ["NYSE:A"], "price": [1.0], "change_pct": [2.0],
                          "change_abs": [0.02], "volume": [5.0], "rel_volume": [1.1], "market_cap": [2e9]})
    store.write_quotes(frame, {"fetched_at": "2026-01-06T15:00:00+00:00", "session": "2026-01-06"})
    back, summary = store.read_quotes()
    assert back.equals(frame) and summary["session"] == "2026-01-06"


# ---------------------------------------------------------- trigger levels
def test_a_forming_double_bottom_triggers_above_its_middle_peak():
    det = next(d for d in detect_chart(from_knots(DOUBLE_BOTTOM[:4] + [(115, 105)]), "T:DB", RULES)
               if d.pattern == "double_bottom")
    assert det.status == "forming"
    assert det.trigger_up == pytest.approx(112 + 0.3, abs=0.2) and math.isnan(det.trigger_down)


def test_a_forming_triangle_triggers_on_both_lines():
    det = next(d for d in detect_chart(from_knots(ASC_TRIANGLE[:8] + [(135, 98.5)]), "T:AT", RULES)
               if d.pattern == "ascending_triangle")
    assert det.status == "forming"
    assert det.trigger_down < 98.5 < det.trigger_up


def test_a_pattern_that_broke_out_has_no_trigger():
    det = next(d for d in detect_chart(from_knots(DOUBLE_BOTTOM), "T:DB", RULES)
               if d.pattern == "double_bottom")
    assert det.status == "breakout" and math.isnan(det.trigger_up) and math.isnan(det.trigger_down)


# ------------------------------------------------------------------ crossings
def _frames():
    stocks = pd.DataFrame({"symbol": ["N:UP", "N:DOWN", "N:INSIDE", "N:OLD", "N:DONE"],
                           "last_date": ["2026-01-05", "2026-01-05", "2026-01-05", "2026-01-02",
                                         "2026-01-05"]})
    detections = pd.DataFrame({
        "symbol": ["N:UP", "N:DOWN", "N:INSIDE", "N:OLD", "N:DONE"],
        "pattern": ["double_bottom", "rising_wedge", "rectangle", "cup_with_handle", "flag"],
        "family": ["chart"] * 5,
        "status": ["forming", "forming", "forming", "forming", "breakout"],
        "trigger_up": [10.0, math.nan, 20.0, 5.0, 1.0],
        "trigger_down": [math.nan, 30.0, 18.0, math.nan, math.nan]})
    return stocks, detections


def test_crossings_need_the_next_session_and_a_level_passed():
    stocks, detections = _frames()
    prices = {"N:UP": 10.5, "N:DOWN": 29.0, "N:INSIDE": 19.0, "N:OLD": 9.0, "N:DONE": 9.0}
    found = crossings(stocks, detections, prices, date(2026, 1, 6))
    assert found[["symbol", "direction"]].values.tolist() == [["N:UP", "bullish"], ["N:DOWN", "bearish"]]
    # N:OLD's last bar is two sessions back: its trigger was for a session already gone
    assert crossings(stocks, detections, prices, date(2026, 1, 7)).empty


def test_no_triggers_in_an_old_scan_means_no_crossings():
    stocks, detections = _frames()
    assert crossings(stocks, detections.drop(columns=["trigger_up", "trigger_down"]),
                     {"N:UP": 99.0}, date(2026, 1, 6)).empty


# ------------------------------------------------------------------ deadline
def test_bars_stop_at_the_deadline_without_opening_a_session(tmp_path):
    job = BarsJob(Store(tmp_path), BarsSettings(history=300, session_batch=2), MarketSettings(),
                  delays=(), now=utc(2026, 9, 23, 22, 0),
                  stop_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    client = FakeClient(FakeOhlcv(date(2026, 9, 23)))
    report = update_all(client, job, ["N:A", "N:B", "N:C"], progress=lambda m: None)
    assert report["deferred"] == 3 and report["calls"] == 0 and client.sessions_opened == 0


# -------------------------------------------------------------------- config
@pytest.mark.parametrize("kw", [{"interval_minutes": 0.5}, {"after_close_minutes": 500},
                                {"stale_after_intervals": 0}])
def test_live_settings_are_checked(kw):
    with pytest.raises(ConfigError):
        LiveSettings(**kw)
