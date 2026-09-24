"""The backfill finds what the daily scans would have found, without looking ahead.
Synthetic bars only (made-up prices)."""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import pytest

from synth import from_knots
from tascreen.backfill import backfill_symbol, run_backfill
from tascreen.config import OutcomesSettings
from tascreen.outcomes import scorecard, update
from tascreen.scan import run_scan
from tascreen.store import Store
from test_chart_patterns import ASC_TRIANGLE, DOUBLE_BOTTOM, HS_TOP, RULES
from test_scan import _universe
from test_web_live import _setup

FLAG = [(0, 50), (60, 50), (66, 60), (68, 58.2), (70, 59.4), (72, 57.6), (74, 58.8), (76, 57.2),
        (78, 62), (79, 62.5)]
FIELDS = ("key", "breakout_day", "breakout_price", "breakout_close", "target", "invalidation", "first_seen")


def _facts(rows):
    def norm(v):
        return None if isinstance(v, float) and math.isnan(v) else (round(v, 6) if isinstance(v, float) else v)
    return sorted(tuple(norm(r[f]) for f in FIELDS) for r in rows)


@pytest.mark.parametrize("bars, min_bars", [
    (from_knots(HS_TOP + [(172, 113), (174, 112)]), 150),        # breaks out, then busts
    (from_knots(ASC_TRIANGLE), 125),
    (from_knots(DOUBLE_BOTTOM), 110),
    (from_knots(FLAG, noise=0.02, wiggle=0.15), 70),              # a flag can be refitted
])
def test_backfill_equals_the_daily_scans_day_by_day(tmp_path, bars, min_bars):
    symbol = "NYSE:SYN"
    bars = bars.assign(symbol=symbol)
    store = Store(tmp_path / "data")
    days = bars["timestamp"].dt.date
    for cut in range(min_bars - 1, len(bars)):
        store.write_bars(symbol, bars.iloc[:cut + 1])
        run_scan(store, _universe([symbol]), date(2025, 1, 2), RULES, days.iloc[cut],
                 progress=lambda m: None)
        update(store, 60)
    daily = store.read_ledger().to_dict("records")
    backfilled = backfill_symbol(bars, symbol, RULES, step=1, min_bars=min_bars)
    assert daily, "the shape should break out"
    assert _facts(backfilled) == _facts(daily)
    assert {r["source"] for r in backfilled} == {"backfill"}


def _bars_only_store(tmp_path):
    store = Store(tmp_path / "data")
    for symbol, knots in (("NYSE:HS", HS_TOP), ("NASDAQ:TRI", ASC_TRIANGLE)):
        store.write_bars(symbol, from_knots(knots).assign(symbol=symbol))
    return store


def test_run_backfill_fills_an_empty_ledger_and_resumes(tmp_path):
    store = _bars_only_store(tmp_path)
    cfg = OutcomesSettings(backfill_workers=1, backfill_min_bars=100)
    first = run_backfill(store, RULES, cfg, ["NYSE:HS", "NASDAQ:TRI"], max_sessions=60,
                         progress=lambda m: None)
    assert first["symbols"] == 2 and first["added_to_ledger"] >= 2 and not first["failed"]
    ledger = store.read_ledger()
    assert set(ledger["source"]) == {"backfill"}
    assert {r["source"] for r in scorecard(ledger, 20)} == {"backfill"}
    again = run_backfill(store, RULES, cfg, ["NYSE:HS", "NASDAQ:TRI"], max_sessions=60,
                         progress=lambda m: None)
    assert again["symbols"] == 0 and again["added_to_ledger"] == 0
    assert len(store.read_ledger()) == len(ledger)
    # another step: the saved rows no longer match and are found again
    restarted = run_backfill(store, RULES, replace(cfg, backfill_step=3), ["NYSE:HS"], max_sessions=60,
                             progress=lambda m: None)
    assert restarted["symbols"] == 1 and store.backfill_done() == {"NYSE_HS"}


def test_backfill_stops_before_the_first_scan_and_never_overrides_it(tmp_path):
    settings, store, day = _setup(tmp_path)
    update(store, 60)
    live = store.read_ledger()
    report = run_backfill(store, RULES, OutcomesSettings(backfill_workers=1, backfill_min_bars=100),
                          ["NYSE:HS", "NASDAQ:DB"], max_sessions=60, progress=lambda m: None)
    assert report["before"] == day.isoformat()
    ledger = store.read_ledger()
    kept = ledger[ledger["key"].isin(live["key"])]
    assert (kept["source"] == "live").all() and len(kept) == len(live)
    # a rebuild from the saved scans keeps the saved backfill too
    rebuilt = update(store, 60, rebuild=True)
    assert rebuilt["rows"] == len(ledger)


def test_the_process_pool_path(tmp_path):
    store = _bars_only_store(tmp_path)
    report = run_backfill(store, RULES, OutcomesSettings(backfill_workers=2, backfill_min_bars=100),
                          ["NYSE:HS", "NASDAQ:TRI"], max_sessions=60, progress=lambda m: None)
    assert report["symbols"] == 2 and not report["failed"] and report["added_to_ledger"] >= 2
