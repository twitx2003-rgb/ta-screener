"""A scanned store with two made-up stocks (a head and shoulders top, and a double
bottom still forming on the last bar), shared by the outcome and backfill tests.
(It lived in the website's tests, removed with the site on 2026-09-27.)"""
from __future__ import annotations

from datetime import date

from synth import from_knots
from tascreen.config import Settings
from tascreen.patterns.rules import load_rules
from tascreen.scan import run_scan
from tascreen.store import Store
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
