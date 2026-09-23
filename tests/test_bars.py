from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pytest

from fakes import FakeClient, FakeOhlcv
from tascreen.bars import BarsJob, merge_bars, update_all
from tascreen.config import BarsSettings, MarketSettings
from tascreen.store import Store

# Wed 2026-09-23, 22:00 UTC = 18:00 New York: the 23rd's session is complete.
AFTER_CLOSE = datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc)
# Wed 2026-09-23, 15:00 UTC = 11:00 New York: the 23rd's bar is a partial session.
DURING = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)
DAY = date(2026, 9, 23)


def _job(tmp_path, now=AFTER_CLOSE, **bars):
    settings = BarsSettings(**{"history": 300, "session_batch": 2, **bars})
    return BarsJob(Store(tmp_path), settings, MarketSettings(), delays=(), now=now)


def _run(job, source, symbols):
    client = FakeClient(source)
    return update_all(client, job, symbols, progress=lambda line: None), client


def test_first_fetch_takes_the_configured_history(tmp_path):
    job, source = _job(tmp_path), FakeOhlcv(DAY)
    report, _ = _run(job, source, ["NASDAQ:AAA"])
    assert report["counts"] == {"new": 1}
    stored = job.store.read_bars("NASDAQ:AAA")
    assert len(stored) == 300 and stored["timestamp"].iloc[-1].date() == DAY
    assert source.calls[0][1]["count"] == 300
    assert job.store.bars_path("NASDAQ:AAA").name == "NASDAQ_AAA.parquet"


def test_partial_session_is_not_stored(tmp_path):
    job, source = _job(tmp_path, now=DURING), FakeOhlcv(DAY)
    _run(job, source, ["NASDAQ:AAA"])
    stored = job.store.read_bars("NASDAQ:AAA")
    assert stored["timestamp"].iloc[-1].date() == date(2026, 9, 22)
    assert job.target == date(2026, 9, 22)


def test_up_to_date_symbols_are_not_called_again(tmp_path):
    job, source = _job(tmp_path), FakeOhlcv(DAY)
    _run(job, source, ["NASDAQ:AAA", "NYSE:BBB"])
    source.calls.clear()
    report, client = _run(job, source, ["NASDAQ:AAA", "NYSE:BBB"])
    assert source.calls == [] and client.sessions_opened == 0
    assert report["counts"] == {"up_to_date": 2}


def test_update_fetches_only_new_sessions_plus_the_overlap(tmp_path):
    # Stored up to Fri 2026-09-18; now it is Wed 23rd after the close: 3 new sessions.
    old = _job(tmp_path, now=datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc))
    _run(old, FakeOhlcv(date(2026, 9, 18)), ["NASDAQ:AAA"])

    job, source = _job(tmp_path), FakeOhlcv(DAY)
    report, _ = _run(job, source, ["NASDAQ:AAA"])
    assert report["counts"] == {"updated": 1}
    assert source.calls[0][1]["count"] == 3 + 5
    stored = job.store.read_bars("NASDAQ:AAA")
    assert stored["timestamp"].iloc[-1].date() == DAY and len(stored) == 303
    assert not stored["timestamp"].dt.date.duplicated().any()


def test_a_split_rescales_history_and_forces_a_full_refetch(tmp_path):
    old = _job(tmp_path, now=datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc))
    _run(old, FakeOhlcv(date(2026, 9, 18)), ["NASDAQ:AAA"])

    job, source = _job(tmp_path), FakeOhlcv(DAY)
    source.scale["NASDAQ:AAA"] = 0.25                   # a 4-for-1 split
    report, _ = _run(job, source, ["NASDAQ:AAA"])
    assert report["counts"] == {"refetched": 1}
    assert "differs" in report["refetched"]["NASDAQ:AAA"]
    assert [c[1]["count"] for c in source.calls] == [8, 300]
    stored = job.store.read_bars("NASDAQ:AAA")
    assert len(stored) == 300 and stored["close"].max() < 50       # all rescaled (unscaled >= 50)


def test_one_failing_symbol_does_not_stop_the_run(tmp_path):
    job, source = _job(tmp_path), FakeOhlcv(DAY)
    source.missing.add("NYSE:BAD")
    report, _ = _run(job, source, ["NASDAQ:AAA", "NYSE:BAD", "NYSE:CCC"])
    assert report["counts"] == {"new": 2, "failed": 1}
    assert "no data" in report["failed"]["NYSE:BAD"]
    assert job.store.read_status()["NYSE:BAD"]["status"] == "failed"
    assert job.store.read_bars("NYSE:BAD") is None


def test_a_symbol_that_stopped_trading_is_kept_but_flagged_stale(tmp_path):
    job, source = _job(tmp_path), FakeOhlcv(date(2026, 9, 16))   # halted after the 16th
    report, _ = _run(job, source, ["NASDAQ:OLD"])
    assert report["counts"] == {"stale": 1}
    assert "older than 2026-09-23" in report["stale"]["NASDAQ:OLD"]


def test_one_session_per_batch(tmp_path):
    job, source = _job(tmp_path, session_batch=2), FakeOhlcv(DAY)
    _, client = _run(job, source, ["A:1", "A:2", "A:3", "A:4", "A:5"])
    assert client.sessions_opened == 3


def test_concurrency_keeps_every_result(tmp_path):
    job, source = _job(tmp_path, concurrency=3, session_batch=10), FakeOhlcv(DAY)
    report, _ = _run(job, source, [f"A:{i}" for i in range(7)])
    assert report["counts"] == {"new": 7}


def test_merge_needs_an_overlap(tmp_path):
    job = _job(tmp_path)
    source = FakeOhlcv(DAY)
    a = asyncio.run(job._fetch(source, "A:1", 300)).iloc[:100]
    b = asyncio.run(job._fetch(source, "A:1", 300)).iloc[200:]
    merged, reason = merge_bars(a, b, 0.05)
    assert merged is None and "no overlapping" in reason


def test_audit_separates_market_wide_closures_from_single_symbol_gaps(tmp_path):
    job, source = _job(tmp_path), FakeOhlcv(DAY)
    symbols = ["A:1", "A:2", "A:3"]
    _run(job, source, symbols)
    closed, halted = date(2026, 9, 10), date(2026, 9, 15)
    for symbol in symbols:                              # every symbol lacks `closed`
        frame = job.store.read_bars(symbol)
        drop = frame["timestamp"].dt.date.eq(closed)
        if symbol == "A:3":
            drop |= frame["timestamp"].dt.date.eq(halted)
        job.store.write_bars(symbol, frame[~drop].reset_index(drop=True))

    from tascreen.bars import audit_bars
    report = audit_bars(job.store, symbols + ["A:NONE"], DAY, min_spanning=2)
    assert report["market_wide_missing_days"] == [closed.isoformat()]
    assert report["symbols_with_gaps"] == {"A:3": [halted.isoformat()]}
    assert report["absent"] == ["A:NONE"] and report["behind_target"] == []
    assert report["bars_on_non_trading_days"] == {}
    # too few symbols span the day for it to count as market-wide
    assert audit_bars(job.store, symbols, DAY)["market_wide_missing_days"] == []


def test_sparse_series_are_reported(tmp_path):
    job, source = _job(tmp_path), FakeOhlcv(DAY)
    _run(job, source, ["A:1"])
    frame = job.store.read_bars("A:1")
    job.store.write_bars("A:1", frame.iloc[::3].reset_index(drop=True))    # every third bar
    from tascreen.bars import audit_bars, coverage
    assert coverage(list(frame["timestamp"].dt.date)) == pytest.approx(1.0)
    report = audit_bars(job.store, ["A:1"], DAY)
    assert 0.3 < report["sparse_series"]["A:1"] < 0.37


@pytest.mark.parametrize("value", [100, 10_000])
def test_history_bounds(value):
    from tascreen.errors import ConfigError

    with pytest.raises(ConfigError):
        BarsSettings(history=value)
