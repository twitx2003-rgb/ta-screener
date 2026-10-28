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
