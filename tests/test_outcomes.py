"""The outcome ledger: invalidation levels, outcomes, ingestion, the scorecard.
Synthetic bars only (made-up prices)."""
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from synth import frame, from_knots
from tascreen.outcomes import (empty_ledger, evaluate_row, merge, rows_from_scan, scorecard,
                               update)
from tascreen.patterns.chart import detect_chart
from tascreen.patterns.levels import detection_key, geometry, invalidation
from test_chart_patterns import ASC_TRIANGLE, CUP, DOUBLE_BOTTOM, HS_TOP, RULES
from test_web_live import _setup


def _one(bars, pattern):
    hits = [d for d in detect_chart(bars, "TEST:SYN", RULES) if d.pattern == pattern]
    assert hits, pattern
    return hits[0].row()


# ------------------------------------------------------------- invalidation
def test_reversal_patterns_fail_beyond_their_extreme_turning_point():
    bottom = _one(from_knots(DOUBLE_BOTTOM), "double_bottom")
    assert invalidation(bottom) == min(p["price"] for p in geometry(bottom, "points"))
    top = _one(from_knots(HS_TOP), "head_shoulders_top")
    head = max(p["price"] for p in geometry(top, "points"))
    assert invalidation(top) == head and geometry(top, "points")[2]["price"] == head


def test_triangles_fail_below_the_whole_pattern_and_flags_below_the_consolidation():
    tri = _one(from_knots(ASC_TRIANGLE), "ascending_triangle")
    lows = [p["price"] for p in geometry(tri, "points")]
    lower_line_end = [ln for ln in geometry(tri, "lines") if ln["label"] == "קו תחתון"][0]["y2"]
    assert tri["direction"] == "bullish" and invalidation(tri) == min(lows) < lower_line_end
    bars = from_knots([(0, 50), (60, 50), (66, 60), (68, 58.2), (70, 59.4), (72, 57.6),
                       (74, 58.8), (76, 57.2), (78, 62), (79, 62.5)], noise=0.02, wiggle=0.15)
    flag = _one(bars, "flag")
    top = pd.Timestamp(geometry(flag, "points")[1]["date"]).date()
    days = bars["timestamp"].dt.date
    assert invalidation(flag, bars) == bars.loc[(days > top) & (days <= flag["end"].date()), "low"].min()
    assert math.isnan(invalidation(flag))                      # needs the bars


def test_cup_fails_below_its_handle_and_forming_two_sided_patterns_have_no_level():
    bars = from_knots(CUP)
    cup = _one(bars, "cup_with_handle")
    lip = pd.Timestamp(geometry(cup, "points")[2]["date"]).date()
    days = bars["timestamp"].dt.date
    handle = bars.loc[(days > lip) & (days <= cup["end"].date()), "low"].min()
    assert invalidation(cup, bars) == handle
    assert math.isnan(invalidation(cup))                       # needs the bars
    forming = _one(from_knots(ASC_TRIANGLE[:8] + [(135, 98.5)]), "ascending_triangle")
    assert math.isnan(invalidation(forming))


def test_the_key_names_symbol_pattern_and_both_end_turning_points():
    row = _one(from_knots(HS_TOP), "head_shoulders_top")
    right_shoulder = geometry(row, "points")[-1]["date"]
    assert detection_key(row) == f"TEST:SYN|head_shoulders_top|{row['start'].date().isoformat()}|{right_shoulder}"
    # a still-forming cup keeps its key while its end moves with each new bar
    forming = _one(from_knots(CUP[:-2] + [(157, 97)]), "cup_with_handle")
    later = _one(from_knots(CUP[:-2] + [(157, 97), (158, 96.5)]), "cup_with_handle")
    assert forming["end"] != later["end"] and detection_key(forming) == detection_key(later)


# ----------------------------------------------------------------- outcomes
def _path(prices, lead=3):
    """Bars: `lead` quiet sessions at 100, then (high, low, close) per session."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * lead
    prev = 100.0
    for high, low, close in prices:
        rows.append((prev, high, low, close))
        prev = close
    return frame(rows)


def _row(bars, **over):
    day = bars["timestamp"].dt.date.iloc[2]            # the breakout day: the 3rd session
    base = {"symbol": "TEST:SYN", "direction": "bullish", "breakout_day": day.isoformat(),
            "breakout_price": 100.0, "breakout_close": 100.0, "target": 110.0, "invalidation": 95.0}
    return {**base, **over}


@pytest.mark.parametrize("prices, outcome, sessions", [
    ([(103, 99, 102), (107, 101, 106), (111, 105, 108)], "target", 3),
    ([(101, 96, 97), (98, 93, 94), (111, 100, 108)], "failed", 2),
    ([(111, 93, 94)], "failed", 1),                                   # both on one bar
    ([(104, 97, 101)] * 5, "expired", 5),
    ([(104, 97, 101)] * 3, "open", 3),
])
def test_first_event_decides(prices, outcome, sessions):
    bars = _path(prices)
    got = evaluate_row(_row(bars), bars, max_sessions=5)
    assert got["outcome"] == outcome and got["sessions"] == sessions
    if outcome in ("target", "failed", "expired"):
        assert got["resolved_day"] == bars["timestamp"].dt.date.iloc[2 + sessions].isoformat()
    else:
        assert got["resolved_day"] is None


def test_bearish_outcomes_mirror():
    bars = _path([(101, 97, 98), (97, 89, 90)])
    got = evaluate_row(_row(bars, direction="bearish", target=90.0, invalidation=105.0), bars, 60)
    assert got["outcome"] == "target" and got["mfe_pct"] == pytest.approx(11.0)


def test_a_split_scales_the_levels():
    bars = _path([(103, 99, 102), (107, 101, 106), (111, 105, 108)])
    halved = bars.assign(**{c: bars[c] / 2 for c in ("open", "high", "low", "close")})
    before, after = evaluate_row(_row(bars), bars, 60), evaluate_row(_row(bars), halved, 60)
    assert after["scale"] == pytest.approx(0.5)
    assert {k: after[k] for k in ("outcome", "sessions", "mfe_pct")} == \
        {k: before[k] for k in ("outcome", "sessions", "mfe_pct")}


def test_missing_bars_are_no_data():
    bars = _path([(103, 99, 102)])
    assert evaluate_row(_row(bars, breakout_day="2020-01-02"), bars, 60)["outcome"] == "no_data"
    assert evaluate_row(_row(bars), None, 60)["outcome"] == "no_data"


def test_failure_day_matches_the_detectors_busted_status():
    bars = from_knots(HS_TOP + [(172, 113), (174, 112)])       # rallies back above the head
    rows = rows_from_scan(pd.DataFrame([_one(bars, "head_shoulders_top")]), date(2025, 1, 2), "d",
                          lambda s: bars)
    assert _one(bars, "head_shoulders_top")["status"] == "busted"
    got = evaluate_row(rows[0], bars, 60)
    head = rows[0]["invalidation"]
    days = bars["timestamp"].dt.date
    after = bars[days > date.fromisoformat(rows[0]["breakout_day"])]
    first_above = after.loc[after["close"] > head, "timestamp"].dt.date.iloc[0]
    assert got["outcome"] == "failed" and got["resolved_day"] == first_above.isoformat()


# ---------------------------------------------------------------- the ledger
def test_update_reads_each_scan_once_and_keeps_first_values(tmp_path):
    settings, store, day = _setup(tmp_path)
    first = update(store, 60)
    assert first["scan_days_read"] == [day.isoformat()] and first["new"] >= 1
    ledger = store.read_ledger()
    assert (ledger["key"].str.startswith("NYSE:HS|head_shoulders_top|")).any()
    again = update(store, 60)
    assert again["scan_days_read"] == [] and again["new"] == 0
    pd.testing.assert_frame_equal(store.read_ledger(), ledger)

    moved = ledger.iloc[0].to_dict()
    moved.update(breakout_day="2030-01-02", breakout_price=moved["breakout_price"] * 1.2)
    merged, counts = merge(ledger, [moved])
    assert counts == {"new": 0, "restated": 1}
    assert merged.iloc[0]["breakout_day"] == ledger.iloc[0]["breakout_day"]
    assert merged.iloc[0]["last_breakout_day"] == "2030-01-02" and merged.iloc[0]["restated"] == 1

    rebuilt = update(store, 60, rebuild=True)
    assert rebuilt["rows"] == len(ledger) and rebuilt["new"] == len(ledger)


def test_a_scan_run_again_for_the_same_day_is_read_again(tmp_path):
    import json as _json

    settings, store, day = _setup(tmp_path)
    update(store, 60)
    path = store.scans_dir / day.isoformat() / "scan.json"
    summary = _json.loads(path.read_text(encoding="utf-8"))
    summary["created_at"] = "2099-01-01T00:00:00+00:00"          # the same day, scanned again
    path.write_text(_json.dumps(summary), encoding="utf-8")
    again = update(store, 60)
    assert again["scan_days_read"] == [day.isoformat()] and again["new"] == 0
    assert update(store, 60)["scan_days_read"] == []


def test_a_rebuild_leaves_out_what_other_rules_found(tmp_path):
    _, store, day = _setup(tmp_path)
    assert update(store, 60)["new"] >= 1
    rebuilt = update(store, 60, rebuild=True, rules_digest="made with other rules")
    assert rebuilt["rows"] == 0 and rebuilt["scans_other_rules"] == [day.isoformat()]
    assert update(store, 60)["scan_days_read"] == []          # marked read: never taken in later
    same = update(store, 60, rebuild=True, rules_digest=RULES.digest)
    assert same["rows"] >= 1 and same["scans_other_rules"] == []
    assert set(store.read_ledger()["rules_digest"]) == {RULES.digest}


def test_a_new_tracking_window_evaluates_every_row_again(tmp_path):
    _, store, _ = _setup(tmp_path)
    update(store, 60)
    update(store, 5)
    assert store.read_outcomes_meta()["max_sessions"] == 5
    assert (store.read_ledger()["sessions"] <= 5).all()


def test_scorecard_shows_percentages_only_with_enough_cases():
    rows = []
    for n, outcome in enumerate(["target"] * 12 + ["failed"] * 8 + ["open"] * 3):
        rows.append({**empty_ledger().iloc[0:0].to_dict(), "key": f"k{n}", "pattern": "double_bottom",
                     "source": "live", "outcome": outcome, "target": 110.0, "sessions": 7.0,
                     "mfe_pct": 5.0})
    ledger = pd.DataFrame(rows)
    [row] = scorecard(ledger, min_cases=20)
    assert row["breakouts"] == 23 and row["decided"] == 20 and row["open"] == 3
    assert row["target_pct"] == 60.0 and row["failed_pct"] == 40.0
    assert scorecard(ledger, min_cases=21)[0]["target_pct"] is None
    assert scorecard(None, 20) == [] and scorecard(empty_ledger(), 20) == []


# --------------------------------------------------------------------- pages
def test_scorecard_page_before_and_after_the_first_ledger(tmp_path):
    from fastapi.testclient import TestClient

    from tascreen.web import fmt
    from tascreen.web.app import HOST, create_app

    settings, store, _ = _setup(tmp_path)
    client = TestClient(create_app(settings, rules=RULES), base_url=f"http://{HOST}")
    empty = client.get("/scorecard")
    assert empty.status_code == 200 and "עוד אין פריצות במעקב" in empty.text
    update(store, 60)
    html = client.get("/scorecard").text
    assert fmt.page_problems(html) == []
    assert "ראש וכתפיים" in html and "לא הסטטיסטיקה מהספרים" in html and "מדגם קטן" in html
    assert 'href="/scorecard" aria-current="page"' in html
    api = client.get("/api/scorecard").json()
    assert api["patterns"][0]["pattern"] == "head_shoulders_top" and api["patterns"][0]["target_pct"] is None
