"""Daily bars for every symbol in the universe, from `get-ohlcv`, kept up to date.

Per symbol (data/bars/<EXCHANGE_TICKER>.parquet):

- first time: `bars.history` bars;
- afterwards: only the sessions since the stored last bar, plus `bars.overlap`
  bars already stored. Those overlapping bars must match what is stored
  (open/high/low/close within `overlap_tolerance_pct`). TradingView's prices
  are split-adjusted only, so a split rescales every past bar: a mismatch means
  the stored history is stale, and the full history is fetched again;
- a symbol whose stored bars already reach the last completed session is not
  called at all, so an interrupted run resumes where it stopped.

Today's bar is dropped until the session has closed (`market.session_close`).
Every frame is checked (`bars_frame`: order, duplicates, OHLC sanity, contract).
One symbol failing is recorded and the run goes on; a sign-in problem stops it.

Calls go through one MCP session per `bars.session_batch` symbols (a session per
call would pay a connection and handshake each time; access tokens last 900 s,
so sessions are not kept open indefinitely).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

import pandas as pd

from .config import BarsSettings, MarketSettings
from .contracts import BARS
from .errors import ContractError, ProviderError
from .market_hours import (drop_incomplete_session, is_trading_day, last_completed_session,
                           sessions_between)
from .store import Store
from .tv.data import OHLCV_TOOL, bars_frame, fetch_in_session

log = logging.getLogger(__name__)

PRICE_COLUMNS = ["open", "high", "low", "close"]


def merge_bars(stored: pd.DataFrame, fresh: pd.DataFrame,
               tolerance_pct: float) -> tuple[pd.DataFrame | None, str]:
    """Append `fresh` to `stored` if their overlapping days agree.

    Returns (merged, "") or (None, reason). Volume is not compared: late prints
    revise it after the close. Fresh values replace stored ones on overlap.
    """
    stored_days = stored["timestamp"].dt.date
    fresh_days = fresh["timestamp"].dt.date
    common = sorted(set(stored_days) & set(fresh_days))
    if not common:
        return None, "no overlapping bar to check against"
    a = stored[stored_days.isin(common)].set_index(stored_days[stored_days.isin(common)])
    b = fresh[fresh_days.isin(common)].set_index(fresh_days[fresh_days.isin(common)])
    rel = ((b[PRICE_COLUMNS] - a[PRICE_COLUMNS]).abs() / a[PRICE_COLUMNS]).max(axis=1) * 100
    worst = rel.idxmax()
    if rel.max() > tolerance_pct:
        return None, (f"overlapping bar {worst} differs by {rel.max():.3f}% "
                      "(split or revised history)")
    first_fresh = fresh_days.min()
    merged = pd.concat([stored[stored_days < first_fresh], fresh], ignore_index=True)
    return BARS.validate(merged), ""


@dataclass
class SymbolResult:
    symbol: str
    status: str                  # new | updated | refetched | up_to_date | stale | failed
    last_date: str | None = None
    rows: int = 0
    calls: int = 0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "last_date": self.last_date, "rows": self.rows,
                "calls": self.calls, "note": self.note}


@dataclass
class BarsJob:
    store: Store
    bars: BarsSettings
    market: MarketSettings
    delays: tuple[float, ...]
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def target(self) -> date:
        return last_completed_session(self.now, market_tz=self.market.timezone,
                                      session_close=self.market.session_close)

    async def _fetch(self, session, symbol: str, count: int) -> pd.DataFrame:
        payload = await fetch_in_session(session, OHLCV_TOOL,
                                         {"symbol": symbol, "interval": "1D", "count": count},
                                         delays=self.delays)
        frame, _dropped = drop_incomplete_session(
            bars_frame(payload, symbol), market_tz=self.market.timezone,
            session_close=self.market.session_close, now=self.now)
        if frame.empty:
            raise ProviderError(f"{OHLCV_TOOL} {symbol}: no completed session in the answer")
        return frame

    async def update_symbol(self, session, symbol: str) -> SymbolResult:
        target = self.target
        result = SymbolResult(symbol, "failed")
        try:
            stored = self.store.read_bars(symbol)
            if stored is not None and stored["timestamp"].iloc[-1].date() >= target:
                result.status = "up_to_date"
                merged = stored
            elif stored is None:
                merged = await self._fetch(session, symbol, self.bars.history)
                result.calls, result.status = 1, "new"
            else:
                last = stored["timestamp"].iloc[-1].date()
                count = sessions_between(last, target) + self.bars.overlap
                fresh = await self._fetch(session, symbol, count)
                result.calls = 1
                merged, reason = merge_bars(stored, fresh, self.bars.overlap_tolerance_pct)
                if merged is None:
                    log.info("%s: %s; fetching the full history again", symbol, reason)
                    merged = await self._fetch(session, symbol, self.bars.history)
                    result.calls, result.status, result.note = 2, "refetched", reason
                else:
                    result.status = "updated"
            if result.status != "up_to_date":
                self.store.write_bars(symbol, merged)
            result.last_date = merged["timestamp"].iloc[-1].date().isoformat()
            result.rows = len(merged)
            if merged["timestamp"].iloc[-1].date() < target:
                # e.g. a halted or delisted stock: kept, but flagged
                result.note = (result.note + "; " if result.note else "") + \
                    f"newest bar {result.last_date} is older than {target.isoformat()}"
                result.status = "stale"
        except (ProviderError, ContractError) as exc:
            result.status, result.note = "failed", str(exc)[:500]
            log.warning("%s: %s", symbol, result.note)
        return result

    async def run_batch(self, session, symbols: list[str]) -> list[SymbolResult]:
        gate = asyncio.Semaphore(self.bars.concurrency)

        async def one(symbol: str) -> SymbolResult:
            async with gate:
                result = await self.update_symbol(session, symbol)
                if result.calls and self.bars.min_interval_s:
                    await asyncio.sleep(self.bars.min_interval_s)
                return result

        return list(await asyncio.gather(*(one(s) for s in symbols)))


def update_all(client: Any, job: BarsJob, symbols: Iterable[str], *,
               progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Update every symbol, one MCP session per batch. Saves status after each batch."""
    symbols = list(symbols)
    status = job.store.read_status()
    results: list[SymbolResult] = []
    started = time.monotonic()
    calls = 0
    for start in range(0, len(symbols), job.bars.session_batch):
        batch = symbols[start:start + job.bars.session_batch]
        done, need = [], []
        for symbol in batch:
            last, rows = _stored_tail(job, symbol)
            if last is not None and date.fromisoformat(last) >= job.target:
                done.append(SymbolResult(symbol, "up_to_date", last, rows))
            else:
                need.append(symbol)
        if need:
            done += client.with_session(lambda session, need=need: job.run_batch(session, need))
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for r in done:
            status[r.symbol] = {**r.as_dict(), "checked_at": checked_at}
        job.store.write_status(status)
        results += done
        calls += sum(r.calls for r in done)
        elapsed = time.monotonic() - started
        per_call = elapsed / calls if calls else 0.0
        remaining = len(symbols) - len(results)
        progress(f"  {len(results)}/{len(symbols)} symbols, {calls} calls, "
                 f"{per_call:.2f} s/call, about {remaining * per_call / 60:.0f} min left "
                 "if the rest need one call each")

    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    elapsed = time.monotonic() - started
    return {
        "target_session": job.target.isoformat(),
        "symbols": len(symbols),
        "calls": calls,
        "seconds": round(elapsed, 1),
        "seconds_per_call": round(elapsed / calls, 2) if calls else None,
        "counts": counts,
        "failed": {r.symbol: r.note for r in results if r.status == "failed"},
        "stale": {r.symbol: r.note for r in results if r.status == "stale"},
        "refetched": {r.symbol: r.note for r in results if r.status == "refetched"},
    }


def coverage(days: list[date]) -> float:
    """Bars held / trading sessions between the first and the last bar.

    Live: a few thinly traded share classes and recently uplisted stocks came back
    with 600 bars spread over many years (coverage 0.2-0.9) — TradingView serves
    some listings from a sparser feed. Patterns on such series are meaningless.
    """
    if len(days) < 2:
        return 1.0
    return len(days) / (sessions_between(days[0], days[-1]) + 1)


def missing_sessions(days: list[date]) -> list[date]:
    """Scheduled trading days between the first and last bar that have no bar."""
    have, out = set(days), []
    day = days[0]
    while day < days[-1]:
        day += timedelta(days=1)
        if is_trading_day(day) and day not in have:
            out.append(day)
    return out


def audit_bars(store: Store, symbols: Iterable[str], target: date,
               market_wide_share: float = 0.5, min_spanning: int = 20) -> dict[str, Any]:
    """Stored bars against the trading calendar.

    - a day missing for most symbols that span it is a closure the calendar does
      not know (or a data outage) — add it to market_hours.UNSCHEDULED_CLOSURES;
    - a day missing for a few symbols is a halt or a data hole;
    - a bar on a non-trading day means the calendar or the data is wrong.
    """
    per_symbol: dict[str, list[date]] = {}
    off_calendar: dict[str, list[str]] = {}
    spans: list[tuple[date, date]] = []
    behind, absent, rows = [], [], []
    sparse: dict[str, float] = {}
    for symbol in symbols:
        stored = store.read_bars(symbol)
        if stored is None:
            absent.append(symbol)
            continue
        days = list(stored["timestamp"].dt.date)
        rows.append(len(days))
        spans.append((days[0], days[-1]))
        share = coverage(days)
        if share < 0.95:
            sparse[symbol] = round(share, 3)
        if days[-1] < target:
            behind.append(symbol)
        gaps = missing_sessions(days)
        if gaps:
            per_symbol[symbol] = gaps
        extra = [d.isoformat() for d in days if not is_trading_day(d)]
        if extra:
            off_calendar[symbol] = extra

    missing_count: dict[date, int] = {}
    for gaps in per_symbol.values():
        for day in gaps:
            missing_count[day] = missing_count.get(day, 0) + 1
    # A day only counts as market-wide if enough symbols span it: a single sparse
    # series reaching back years would otherwise make each of its holes "100%".
    min_spanning = max(min_spanning, int(0.05 * len(spans)))
    market_wide = set()
    for day, n in missing_count.items():
        spanning = sum(1 for first, last in spans if first <= day <= last)
        if spanning >= min_spanning and n / spanning >= market_wide_share:
            market_wide.add(day)
    return {
        "target_session": target.isoformat(),
        "symbols_checked": len(spans),
        "absent": absent,
        "behind_target": behind,
        "market_wide_missing_days": sorted(d.isoformat() for d in market_wide),
        "symbols_with_gaps": {s: [d.isoformat() for d in g if d not in market_wide]
                              for s, g in per_symbol.items() if set(g) - market_wide},
        "bars_on_non_trading_days": off_calendar,
        "sparse_series": dict(sorted(sparse.items(), key=lambda kv: kv[1])),
        "rows_min": min(rows) if rows else 0,
        "rows_max": max(rows) if rows else 0,
    }


def _stored_tail(job: BarsJob, symbol: str) -> tuple[str | None, int]:
    stored = job.store.read_bars(symbol)
    if stored is None:
        return None, 0
    return stored["timestamp"].iloc[-1].date().isoformat(), len(stored)
