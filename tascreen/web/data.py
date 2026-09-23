"""The newest scan, loaded once and reloaded when a newer one is written.

`run.py --scan` writes scan.json last, so its presence and modification time
say which complete scan is current; a scan finishing while the site is open is
picked up on the next request.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

import pandas as pd

from ..market_hours import sessions_between
from ..patterns.rules import Rules
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
