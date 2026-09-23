"""The website over a synthetic scan (made-up prices). No network, no TradingView."""
from __future__ import annotations

import json
import re
from datetime import date

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import QueryParams

from synth import from_knots
from tascreen.config import ConfigError, Settings, WebSettings, load_settings
from tascreen.patterns.rules import load_rules
from tascreen.scan import run_scan
from tascreen.store import Store
from tascreen.web import fmt, labels
from tascreen.web.app import HOST, create_app
from tascreen.web.data import ScanView
from tascreen.web.filters import Query, apply, parse_query
from test_chart_patterns import DOUBLE_BOTTOM, HS_TOP
from test_scan import _universe

RULES = load_rules()


def _scan(tmp_path):
    settings = Settings(root=tmp_path)
    store = Store(settings.data_dir)
    hs = from_knots(HS_TOP)
    store.write_bars("NYSE:HS", hs.assign(symbol="NYSE:HS"))
    store.write_bars("NASDAQ:DB", from_knots(DOUBLE_BOTTOM).assign(symbol="NASDAQ:DB"))
    store.write_bars("NYSE:THIN", hs.iloc[::2].reset_index(drop=True).assign(symbol="NYSE:THIN"))
    universe = _universe(["NYSE:HS", "NASDAQ:DB", "NYSE:THIN", "NYSE:NOBARS"])
    run_scan(store, universe, date(2025, 9, 1), RULES, hs["timestamp"].iloc[-1].date(),
             progress=lambda m: None)
    return settings


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(_scan(tmp_path), rules=RULES), base_url=f"http://{HOST}")


def _html_ok(response):
    assert response.status_code == 200, response.text[:500]
    assert fmt.page_problems(response.text) == []
    return response.text


# ------------------------------------------------------------------ pages
@pytest.mark.parametrize("url", [
    "/", "/?family=chart", "/?family=candle&direction=bullish&within=3",
    "/?pattern=head_shoulders_top&status=breakout", "/?rsi_min=20&rsi_max=80&sma50=above",
    "/?sort=rsi14&order=asc", "/?sort=age", "/?page=99", "/?q=hs",
    "/patterns", "/status", "/symbol/NYSE:HS", "/symbol/NASDAQ:DB", "/symbol/NYSE:THIN",
])
def test_every_page_renders_without_raw_nan_or_none(client, url):
    html = _html_ok(client.get(url))
    assert 'dir="rtl"' in html and "לא ייעוץ השקעות" in html
    assert "Copyright (c) 2025 TradingView, Inc." in html      # lightweight-charts NOTICE


def test_pattern_filter_keeps_only_stocks_with_a_matching_detection(client):
    html = _html_ok(client.get("/?pattern=head_shoulders_top"))
    assert "/symbol/NYSE:HS" in html and "/symbol/NASDAQ:DB" not in html
    data = client.get("/api/scan?pattern=head_shoulders_top").json()
    assert data["total"] == 1
    [row] = data["results"]
    assert row["symbol"] == "NYSE:HS"
    assert {d["pattern"] for d in row["detections"]} == {"head_shoulders_top"}


def test_without_pattern_filters_every_scanned_stock_is_listed(client):
    data = client.get("/api/scan").json()
    assert {r["symbol"] for r in data["results"]} == {"NYSE:HS", "NASDAQ:DB", "NYSE:THIN"}
    assert "not investment advice" in data["note"]


def test_bad_filter_values_are_reported_not_applied(client):
    data = client.get("/api/scan?rsi_min=abc&pattern=nope&family=weird").json()
    assert data["total"] == 3 and len(data["errors"]) == 3
    html = _html_ok(client.get("/?rsi_min=abc"))
    assert "RSI מינימלי" in html


def test_symbol_page_embeds_chart_data_with_lines_on_real_sessions(client):
    html = _html_ok(client.get("/symbol/NYSE:HS"))
    raw = re.search(r'<script type="application/json" id="chart-data">(.*?)</script>', html, re.S)
    chart = json.loads(raw.group(1))
    days = {c["time"] for c in chart["candles"]}
    hs = next(d for d in chart["detections"] if d["pattern"] == "head_shoulders_top")
    assert hs["lines"] and all(ln["x1"] in days and ln["x2"] in days for ln in hs["lines"])
    assert all(p["date"] in days for p in hs["points"])
    assert len(chart["sma50"]) == len(chart["candles"]) - 49 and chart["sma200"] == []
    assert "כלל המדידה של הספר, לא תחזית" in html
    assert 'id="p-head_shoulders_top"' in html                  # screener chips link here


def test_sparse_symbol_says_why_it_has_no_patterns(client):
    html = _html_ok(client.get("/symbol/NYSE:THIN"))
    assert "לא חיפשנו בה תבניות" in html


@pytest.mark.parametrize("url", ["/symbol/NYSE:NOPE", "/symbol/NOBARS", "/symbol/NYSE:NOBARS"])
def test_unknown_symbols_are_404(client, url):
    response = client.get(url)
    assert response.status_code == 404 and fmt.page_problems(response.text) == []


def test_api_symbol_returns_checklists(client):
    data = client.get("/api/symbol/NYSE:HS").json()
    hs = next(d for d in data["detections"] if d["pattern"] == "head_shoulders_top")
    assert hs["checks"] and {"rule", "label_he", "value", "threshold", "passed", "origin"} <= set(hs["checks"][0])
    assert client.get("/api/symbol/NYSE:NOPE").status_code == 404


def test_a_foreign_host_name_is_refused(client):
    assert client.get("/", headers={"host": "example.com"}).status_code == 400


def test_no_scan_yet(tmp_path):
    client = TestClient(create_app(Settings(root=tmp_path), rules=RULES), base_url=f"http://{HOST}")
    assert "אין עדיין סריקה" in _html_ok(client.get("/"))
    _html_ok(client.get("/status"))
    _html_ok(client.get("/patterns"))
    assert client.get("/api/scan").status_code == 503


def test_a_newer_scan_is_picked_up_without_a_restart(tmp_path):
    settings = _scan(tmp_path)
    client = TestClient(create_app(settings, rules=RULES), base_url=f"http://{HOST}")
    first = client.get("/api/scan").json()["scan_session"]
    store = Store(settings.data_dir)
    indicators, patterns, summary = store.read_scan(store.scan_days()[-1])
    store.write_scan(date(2031, 1, 2), indicators, patterns, {**summary, "scan_session": "2031-01-02"})
    assert client.get("/api/scan").json()["scan_session"] == "2031-01-02" != first


def test_changed_rules_are_flagged(tmp_path):
    settings = _scan(tmp_path)
    changed = load_rules()
    object.__setattr__(changed, "digest", "not-the-scan")
    client = TestClient(create_app(settings, rules=changed), base_url=f"http://{HOST}")
    assert "rules.yaml השתנה מאז הסריקה" in _html_ok(client.get("/"))
    assert client.get("/api/scan").json()["rules_changed_since_scan"] is True


# ---------------------------------------------------------------- filters
def _view():
    stocks = pd.DataFrame({
        "symbol": ["NYSE:AAA", "NASDAQ:BBB", "NYSE:CCC"], "ticker": ["AAA", "BBB", "CCC"],
        "description": ["Alpha Corp", "Beta Inc", "Gamma plc"], "exchange": ["NYSE", "NASDAQ", "NYSE"],
        "sector": ["Finance", "Utilities", "Finance"], "market_cap": [5e9, 50e9, 1.5e9],
        "rsi14": [25.0, 55.0, np.nan], "above_sma50": [True, False, None],
        "above_sma200": [None, True, False], "rel_volume": [2.0, 0.8, 1.2],
        "pct_from_52w_high": [-2.0, -30.0, -8.0], "atr_pct": [1.0, 3.0, 5.0],
        "avg_dollar_volume_20d": [50e6, 5e6, 20e6],
        "golden_cross_days_ago": [3.0, np.nan, np.nan], "death_cross_days_ago": [np.nan, 7.0, np.nan],
        "change_1d_pct": [1.0, -2.0, 0.5], "change_20d_pct": [5.0, -1.0, np.nan],
    })
    detections = pd.DataFrame({
        "symbol": ["NYSE:AAA", "NYSE:AAA", "NASDAQ:BBB"], "family": ["chart", "candle", "chart"],
        "pattern": ["double_bottom", "hammer", "rising_wedge"], "name_he": ["a", "b", "c"],
        "direction": ["bullish", "bullish", "bearish"], "status": ["breakout", "signal", "forming"],
        "age": [4, 0, 12], "event_day": [date(2026, 1, 5)] * 3,
        "breakout_price": [1.0, np.nan, np.nan], "target": [2.0, np.nan, np.nan],
    })
    return ScanView(date(2026, 1, 9), {}, stocks, detections)


def _run(url_query: str):
    return apply(_view(), parse_query(QueryParams(url_query), RULES), per_page=50)


@pytest.mark.parametrize("query, expected", [
    ("", ["NASDAQ:BBB", "NYSE:AAA", "NYSE:CCC"]),                  # market cap, largest first
    ("mcap_min=2&mcap_max=10", ["NYSE:AAA"]),
    ("sector=Finance", ["NYSE:AAA", "NYSE:CCC"]),
    ("rsi_max=30", ["NYSE:AAA"]),                                  # NaN RSI never passes
    ("sma50=above", ["NYSE:AAA"]), ("sma50=below", ["NASDAQ:BBB"]),
    ("sma200=below", ["NYSE:CCC"]),                                # None (too few bars) passes neither
    ("near_high=5", ["NYSE:AAA"]), ("relvol_min=1.1", ["NYSE:AAA", "NYSE:CCC"]),
    ("atr_min=2&atr_max=4", ["NASDAQ:BBB"]), ("dollar_vol_min=10", ["NYSE:AAA", "NYSE:CCC"]),
    ("cross=golden", ["NYSE:AAA"]), ("cross=death", ["NASDAQ:BBB"]),
    ("q=beta", ["NASDAQ:BBB"]), ("q=nyse:c", ["NYSE:CCC"]),
    ("family=any", ["NASDAQ:BBB", "NYSE:AAA"]), ("family=candle", ["NYSE:AAA"]),
    ("direction=bearish", ["NASDAQ:BBB"]), ("status=forming&status=breakout", ["NASDAQ:BBB", "NYSE:AAA"]),
    ("within=5", ["NYSE:AAA"]), ("within=1", ["NYSE:AAA"]),
    ("pattern=rising_wedge&pattern=hammer", ["NASDAQ:BBB", "NYSE:AAA"]),
    ("sort=age", ["NYSE:AAA", "NASDAQ:BBB", "NYSE:CCC"]),         # no detection sorts last
    ("sort=rsi14&order=asc", ["NYSE:AAA", "NASDAQ:BBB", "NYSE:CCC"]),
])
def test_filters(query, expected):
    assert [r["symbol"] for r in _run(query).rows] == expected


def test_shown_detections_are_the_matching_ones():
    rows = _run("family=chart").rows
    assert [m["pattern"] for m in rows[1]["matches"]] == ["double_bottom"]    # not the hammer
    everything = _run("").rows
    assert len(next(r for r in everything if r["symbol"] == "NYSE:AAA")["matches"]) == 2


def test_within_counts_trading_sessions_from_the_scan_session():
    assert [m["pattern"] for m in _run("within=1").rows[0]["matches"]] == ["hammer"]


def test_paging():
    view = _view()
    result = apply(view, Query(page=2), per_page=2)
    assert result.total == 3 and result.pages == 2 and len(result.rows) == 1
    assert apply(view, Query(page=9), per_page=2).page == 2


def test_query_survives_a_round_trip_through_its_url():
    q = parse_query(QueryParams("pattern=hammer&pattern=flag&status=breakout&rsi_min=30.5"
                                "&sma200=above&sort=rsi14&order=asc&page=3"), RULES)
    back = parse_query(QueryParams(q.url(page=3).split("?", 1)[1]), RULES)
    assert back == q and not q.errors


def test_sort_link_flips_the_order_of_the_current_column():
    q = parse_query(QueryParams("sort=rsi14"), RULES)
    assert "order=asc" in q.sort_url("rsi14")
    assert "order" not in q.sort_url("market_cap")


def test_out_of_range_values_are_errors():
    q = parse_query(QueryParams("rsi_min=150&within=0&near_high=-1&mcap_min=nan&sort=bogus"), RULES)
    assert len(q.errors) == 5 and q.rsi_min is None and q.within is None and q.sort == "market_cap"


def test_active_filters_can_each_be_removed():
    q = parse_query(QueryParams("pattern=hammer&rsi_max=30&sector=Finance"), RULES)
    active = dict(q.active(RULES))
    assert set(active) == {"פטיש", "RSI מרבי: 30", "סקטור: פיננסים"}
    assert "hammer" not in active["פטיש"] and "rsi_max=30" in active["פטיש"]


# ------------------------------------------------------------- formatting
def test_formatting_never_prints_nan():
    for value in (None, float("nan"), float("inf"), "x", True):
        assert fmt.num(value) == fmt.pct(value) == fmt.money(value) == fmt.price(value) == "—"
    assert fmt.day(pd.NaT) == fmt.day(None) == fmt.day("bad") == "—"
    assert fmt.money(1.234e12) == "$1.23T" and fmt.money(45.6e9) == "$45.6B"
    assert fmt.pct(-1.25) == "-1.2%" and fmt.pct(3) == "+3.0%"
    assert fmt.day("2026-09-22") == "22/09/2026"


def test_clean_makes_json_safe_values():
    out = fmt.clean({"a": np.float64("nan"), "b": np.int64(3), "c": pd.NaT,
                     "d": pd.Timestamp("2026-01-02", tz="UTC"), "e": [np.bool_(True), float("inf")]})
    assert out == {"a": None, "b": 3, "c": None, "d": "2026-01-02T00:00:00+00:00", "e": [True, None]}
    assert "NaN" not in fmt.script_json({"x": float("nan"), "s": "</script>"})
    assert "</script>" not in fmt.script_json({"s": "</script>"})


def test_page_check_catches_leaks():
    assert fmt.page_problems("<p>RSI nan</p>")
    assert fmt.page_problems('<input value="None">')
    assert fmt.page_problems('<script type="application/json">{"x": NaN}</script>')
    assert fmt.page_problems("<td>NaT</td>")
    assert not fmt.page_problems("<p>information, nano, Nancy</p><script>const n = null;</script>")


def test_check_values_in_hebrew():
    assert labels.check_text(None) == "טרם"
    assert labels.check_text(True) == "כן"
    assert labels.check_text("outside a line") == "מחוץ לאחד הקווים"
    assert labels.check_text(">= 2.0x body") == "פי 2.0 מהגוף לפחות"
    assert labels.check_text("open < 88.06, close > 88.08") == "פתיחה < 88.06, סגירה > 88.08"
    assert labels.check_text("<= 0.75") == "<= 0.75"


def test_every_rule_parameter_has_a_hebrew_label():
    names = set(RULES.general) | set(RULES.candle_general)
    for spec in {**RULES.chart, **RULES.candle}.values():
        names |= set(spec.params)
    assert names - set(labels.PARAMS) == set()


# ---------------------------------------------------------------- binding
def test_the_host_is_not_a_setting(tmp_path):
    assert HOST == "127.0.0.1"
    assert "host" not in {f for f in WebSettings.__dataclass_fields__}
    (tmp_path / "config.yaml").write_text("web:\n  host: 0.0.0.0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "config.yaml", root=tmp_path)
