from __future__ import annotations

import math
from datetime import date

import pandas as pd

from fakes import stock_row
from synth import from_knots
from tascreen.contracts import UNIVERSE
from tascreen.patterns.rules import load_rules
from tascreen.scan import run_scan
from tascreen.store import Store
from tascreen.universe import universe_row
from test_chart_patterns import HS_TOP


def _universe(symbols):
    rows = [universe_row(stock_row(i, (2 + i) * 1e9), "t") for i in range(len(symbols))]
    for row, symbol in zip(rows, symbols):
        row["symbol"], row["exchange"] = symbol, symbol.split(":")[0]
    return UNIVERSE.validate(pd.DataFrame(rows, columns=list(UNIVERSE.columns)))


def test_scan_writes_indicators_patterns_and_a_summary(tmp_path):
    store = Store(tmp_path)
    bars = from_knots(HS_TOP)
    store.write_bars("NYSE:HS", bars.assign(symbol="NYSE:HS"))
    last = bars["timestamp"].iloc[-1].date()
    universe = _universe(["NYSE:HS", "NYSE:NOBARS"])

    summary = run_scan(store, universe, date(2025, 9, 1), load_rules(), last, progress=lambda m: None)

    assert summary["symbols_scanned"] == 1
    assert summary["symbols_without_bars"] == ["NYSE:NOBARS"]
    assert summary["counts"]["head_shoulders_top"] == {"breakout": 1}
    assert summary["rules_digest"] and summary["scan_session"] == last.isoformat()
    assert store.scan_days() == [last]

    indicators, patterns, saved = store.read_scan(last)
    assert indicators.loc[0, "symbol"] == "NYSE:HS" and indicators.loc[0, "market_cap"] > 0
    assert "sector" in indicators.columns and not math.isnan(indicators.loc[0, "rsi14"])
    assert (patterns["symbol"] == "NYSE:HS").all()
    assert saved["detections"] == len(patterns)


def test_sparse_series_get_indicators_but_no_patterns(tmp_path):
    store = Store(tmp_path)
    bars = from_knots(HS_TOP)
    store.write_bars("NYSE:THIN", bars.iloc[::2].reset_index(drop=True).assign(symbol="NYSE:THIN"))
    last = bars["timestamp"].iloc[-1].date()
    summary = run_scan(store, _universe(["NYSE:THIN"]), date(2025, 9, 1), load_rules(), last,
                       progress=lambda m: None)
    assert summary["symbols_too_sparse_for_patterns"] == ["NYSE:THIN"]
    assert summary["detections"] == 0 and summary["symbols_scanned"] == 1


def test_a_symbol_behind_the_session_is_scanned_and_flagged(tmp_path):
    store = Store(tmp_path)
    bars = from_knots(HS_TOP)
    store.write_bars("NYSE:OLD", bars.assign(symbol="NYSE:OLD"))
    later = date(2030, 1, 2)
    summary = run_scan(store, _universe(["NYSE:OLD"]), date(2025, 9, 1), load_rules(), later,
                       progress=lambda m: None)
    assert summary["symbols_behind_session"] == ["NYSE:OLD"]
