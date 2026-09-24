"""One scan: indicators, candlestick and chart patterns for every symbol in the universe.

Reads the newest universe and the stored bars; writes data/scans/<session>/ where
<session> is the last completed trading session. Nothing here calls TradingView,
so a scan can be rerun at will (for example after a change to rules.yaml).

A symbol whose bars end before that session is still scanned (its last date is
recorded and shown); a symbol with no bars, or whose bars fail their contract,
is listed in the summary and skipped.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Callable

import pandas as pd

from .bars import coverage
from .contracts import PATTERNS
from .errors import ContractError, ProviderError
from .indicators import cross_check, latest
from .patterns.candles import detect_candles
from .patterns.chart import detect_chart
from .patterns.rules import Rules
from .store import Store

log = logging.getLogger(__name__)

UNIVERSE_COLUMNS = ["symbol", "exchange", "ticker", "description", "sector", "industry",
                    "market_cap", "tv_rsi", "tv_ema50", "tv_ema200", "tv_rating", "next_earnings"]


def patterns_allowed(bars: pd.DataFrame, rules: Rules) -> tuple[float, bool]:
    """(coverage of the recent bars, whether patterns may be looked for): holes in the
    series would fake the shapes."""
    recent = bars["timestamp"].dt.date.iloc[-int(rules.g("coverage_sessions")):].tolist()
    share = round(coverage(recent), 4)
    return share, share >= rules.g("min_recent_coverage")


def patterns_frame(detections: list) -> pd.DataFrame:
    """Detections as a PATTERNS frame (typed columns)."""
    patterns = pd.DataFrame([d.row() for d in detections], columns=list(PATTERNS.columns))
    for column in ("start", "end"):
        patterns[column] = pd.to_datetime(patterns[column], utc=True)
    for column in ("breakout_price", "height", "target", "trigger_up", "trigger_down"):
        patterns[column] = pd.to_numeric(patterns[column])
    return patterns


def scan_symbol(bars: pd.DataFrame, symbol: str, rules: Rules) -> tuple[dict[str, Any], list]:
    values = latest(bars)
    values["coverage"], allowed = patterns_allowed(bars, rules)
    values["patterns_skipped"] = not allowed
    if not allowed:
        return values, []
    detections = detect_candles(bars, symbol, rules) + detect_chart(bars, symbol, rules)
    return values, detections


def run_scan(store: Store, universe: pd.DataFrame, universe_day: date, rules: Rules,
             target: date, *, progress: Callable[[str], None] = print) -> dict[str, Any]:
    started = time.monotonic()
    rows, detections, missing, errors, behind = [], [], [], {}, []
    symbols = universe["symbol"].tolist()
    for n, symbol in enumerate(symbols, 1):
        try:
            bars = store.read_bars(symbol)
        except (ContractError, ProviderError, OSError) as exc:
            errors[symbol] = str(exc)[:300]
            continue
        if bars is None:
            missing.append(symbol)
            continue
        try:
            values, found = scan_symbol(bars, symbol, rules)
        except Exception as exc:  # noqa: BLE001 — one symbol must not stop the scan
            log.exception("%s: scan failed", symbol)
            errors[symbol] = f"{type(exc).__name__}: {exc}"[:300]
            continue
        if date.fromisoformat(values["last_date"]) < target:
            behind.append(symbol)
        rows.append({"symbol": symbol, **values})
        detections += found
        if n % 250 == 0:
            progress(f"  scanned {n}/{len(symbols)}")

    indicators = pd.DataFrame(rows)
    if indicators.empty:
        raise ProviderError("scan: no symbol had bars; run --bars first")
    indicators = universe[UNIVERSE_COLUMNS].merge(indicators, on="symbol", how="inner")
    patterns = patterns_frame(detections)

    counts: dict[str, dict[str, int]] = {}
    for (pattern, status), n in Counter(zip(patterns["pattern"], patterns["status"])).items():
        counts.setdefault(pattern, {})[status] = n
    summary = {
        "scan_session": target.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules_digest": rules.digest,
        "universe_day": universe_day.isoformat(),
        "symbols_in_universe": len(symbols),
        "symbols_scanned": int(len(indicators)),
        "symbols_without_bars": missing,
        "symbols_behind_session": behind,
        "symbols_too_sparse_for_patterns": indicators.loc[indicators["patterns_skipped"], "symbol"].tolist(),
        "errors": errors,
        "detections": int(len(patterns)),
        "counts": dict(sorted(counts.items())),
        "cross_check_vs_tradingview": cross_check(indicators),
        "seconds": round(time.monotonic() - started, 1),
    }
    store.write_scan(target, indicators, patterns, summary)
    return summary
