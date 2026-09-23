"""The newest scan and the newest live quotes, reloaded when newer ones are written.

`run.py --scan` writes scan.json last, and `--quotes`/`--live` write latest.json
last, so each file's presence and modification time say which complete set is
current; a scan or a quote refresh finishing while the site is open is picked up
on the next request.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

import pandas as pd

from ..market_hours import sessions_between
from ..patterns.rules import Rules
from ..quotes import crossings
from ..store import Store
from . import labels


@lru_cache(maxsize=4096)
def _age(event: date, session: date) -> int:
    """Trading sessions from an event to the scan session (0 = that session)."""
    return sessions_between(event, session) if event < session else 0


@dataclass(frozen=True)
class ScanView:
    day: date                      # the scan's session
    summary: dict[str, Any]
    stocks: pd.DataFrame           # one row per symbol (indicators + universe columns)
    detections: pd.DataFrame       # one row per detection, plus name_he/event_day/age

    def stock(self, symbol: str) -> dict[str, Any] | None:
        rows = self.stocks.loc[self.stocks["symbol"] == symbol]
        return rows.iloc[0].to_dict() if len(rows) else None

    def detections_of(self, symbol: str) -> list[dict[str, Any]]:
        rows = self.detections.loc[self.detections["symbol"] == symbol]
        return [detection_record(row) for row in rows.to_dict("records")]


def detection_record(row: dict[str, Any]) -> dict[str, Any]:
    """A detection row with its JSON columns parsed."""
    out = {k: v for k, v in row.items() if not k.endswith("_json")}
    out["points"] = json.loads(row["points_json"] or "[]")
    out["lines"] = json.loads(row["lines_json"] or "[]")
    out["checks"] = json.loads(row["checks_json"] or "[]")
    return out


def _prepare(indicators: pd.DataFrame, patterns: pd.DataFrame, rules: Rules,
             session: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    stocks = indicators.copy()
    stocks["sector_he"] = stocks["sector"].map(labels.sector)
    det = patterns.copy()
    known = {**rules.chart, **rules.candle}
    det["name_he"] = det["pattern"].map(lambda k: known[k].name_he if k in known else k)
    det["name_en"] = det["pattern"].map(lambda k: known[k].name_en if k in known else k)
    event = det["breakout_date"].where(det["breakout_date"].notna(), det["end"])
    det["event_day"] = [ts.date() for ts in event]
    det["age"] = [_age(d, session) for d in det["event_day"]]
    # chart patterns before candles; within each, breakouts first, then the most recent
    det["_family"] = det["family"].map({"chart": 0, "candle": 1}).fillna(9)
    det["_status"] = det["status"].map({"breakout": 0, "busted": 1, "signal": 2, "forming": 3}).fillna(9)
    det = det.sort_values(["symbol", "_family", "_status", "age"], kind="stable")
    det = det.drop(columns=["_family", "_status"])
    return stocks.reset_index(drop=True), det.reset_index(drop=True)


@dataclass(frozen=True)
class LiveQuotes:
    """The newest quotes file, as the site uses it."""
    summary: dict[str, Any]
    prices: dict[str, float]
    changes: dict[str, float]
    fetched_at: datetime
    session: date | None           # the trading day they belong to; None = market closed


@dataclass(frozen=True)
class Live:
    """Quotes that apply to a scan: from the session after it, and fresh."""
    quotes: LiveQuotes
    active: bool                   # fresh, and newer than the scan's session
    stale: bool                    # from a later session, but no longer refreshing
    crossings: dict[str, list[dict[str, Any]]]   # symbol -> crossings (active only)

    @property
    def crossing_symbols(self) -> set[str]:
        return set(self.crossings)


def live_for(view: ScanView | None, quotes: LiveQuotes | None, now: datetime,
             max_age: timedelta) -> Live | None:
    if view is None or quotes is None or quotes.session is None or quotes.session <= view.day:
        return None                # no session running, or the scan already has its close
    fresh = now - quotes.fetched_at <= max_age
    found: dict[str, list[dict[str, Any]]] = {}
    if fresh:
        names = dict(zip(view.detections["pattern"], view.detections["name_he"]))
        table = crossings(view.stocks, view.detections, quotes.prices, quotes.session)
        for row in table.to_dict("records"):
            found.setdefault(row["symbol"], []).append({**row, "name_he": names.get(row["pattern"], row["pattern"])})
    return Live(quotes, active=fresh, stale=not fresh, crossings=found)


def with_live_prices(view: ScanView, live: Live | None) -> ScanView:
    """The scan with today's live price and change in place of the last close's,
    so the table, the filters and the sort all use them (`live` marks the rows)."""
    if live is None or not live.active:
        return view
    stocks = view.stocks.copy()
    price = stocks["symbol"].map(live.quotes.prices)
    change = stocks["symbol"].map(live.quotes.changes)
    has = price.notna()
    stocks["last_close"] = stocks["close"]
    stocks.loc[has, "close"] = price[has]
    stocks.loc[has, "change_1d_pct"] = change[has]
    stocks["live"] = has
    return ScanView(view.day, view.summary, stocks, view.detections)


class QuotesRepository:
    def __init__(self, store: Store):
        self.store = store
        self._lock = threading.Lock()
        self._key: int | None = None
        self._quotes: LiveQuotes | None = None

    def current(self) -> LiveQuotes | None:
        meta = self.store.quotes_dir / "latest.json"
        if not meta.exists():
            return None
        stamp = meta.stat().st_mtime_ns
        with self._lock:
            if self._key != stamp:
                found = self.store.read_quotes()
                if found is None:
                    return None
                frame, summary = found
                session = summary.get("session")
                self._quotes = LiveQuotes(
                    summary=summary,
                    prices={s: float(p) for s, p in zip(frame["symbol"], frame["price"]) if p == p},
                    changes={s: float(c) for s, c in zip(frame["symbol"], frame["change_pct"]) if c == c},
                    fetched_at=datetime.fromisoformat(summary["fetched_at"]),
                    session=date.fromisoformat(session) if session else None)
                self._key = stamp
            return self._quotes


class ScanRepository:
    def __init__(self, store: Store, rules: Rules):
        self.store, self.rules = store, rules
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._view: ScanView | None = None

    def current(self) -> ScanView | None:
        days = self.store.scan_days()
        if not days:
            return None
        day = days[-1]
        stamp = (self.store.scans_dir / day.isoformat() / "scan.json").stat().st_mtime_ns
        with self._lock:
            if self._key != (day, stamp):
                indicators, patterns, summary = self.store.read_scan(day)
                stocks, detections = _prepare(indicators, patterns, self.rules, day)
                self._view = ScanView(day, summary, stocks, detections)
                self._key = (day, stamp)
            return self._view
