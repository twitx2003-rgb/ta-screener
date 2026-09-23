"""Look at the live server before mapping anything (`run.py --discover`).

The screener's response shape, its column catalogue and whether it offers
candlestick / pattern columns are unknown: the tool schemas say what goes in,
not what comes out. Each probe below is one read-only call; its payload (or its
error) is saved to logs/discover/<name>.json and summarised on screen. The code
that reads these tools is written from those files, not from guesses.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ProviderError
from .tv.data import (COLUMNS_TOOL, OHLCV_TOOL, SCREENER_TOOL, RateLimited, fetch_in_session,
                      save_payload)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Probe:
    name: str
    tool: str
    arguments: dict[str, Any]
    why: str


_UNIVERSE = {"market": "america", "symbol_types": ["stock"]}

PROBES: tuple[Probe, ...] = (
    Probe("columns_catalog", COLUMNS_TOOL, {"market": "stock"},
          "what the column catalogue looks like without a group or search"),
    Probe("columns_technicals", COLUMNS_TOOL, {"market": "stock", "group": "technicals"},
          "indicator columns (RSI, SMA, ...) with their timeframe variants"),
    Probe("columns_candle", COLUMNS_TOOL, {"market": "stock", "search": "candle"},
          "does the screener offer candlestick-pattern columns"),
    Probe("columns_pattern", COLUMNS_TOOL, {"market": "stock", "search": "pattern"},
          "does it offer any chart-pattern columns"),
    Probe("columns_exchange", COLUMNS_TOOL, {"market": "stock", "search": "exchange"},
          "the column that names the listing exchange (to drop OTC)"),
    Probe("columns_type", COLUMNS_TOOL, {"market": "stock", "search": "type"},
          "type/subtype columns (common stock vs ADR vs preferred)"),
    Probe("screener_top5", SCREENER_TOOL,
          {**_UNIVERSE, "filters": {"market_cap_basic": [1e9, None]},
           "sort_by": "market_cap_basic", "sort_order": "desc", "limit": 5},
          "the screener's response shape, default columns and totalCount for > $1B"),
    Probe("screener_band", SCREENER_TOOL,
          {**_UNIVERSE, "filters": {"market_cap_basic": [1e9, 2e9]},
           "sort_by": "market_cap_basic", "sort_order": "desc", "limit": 3},
          "whether a market-cap band narrows totalCount (needed: limit caps at 1000)"),
    Probe("ohlcv_sample", OHLCV_TOOL, {"symbol": "NASDAQ:AAPL", "interval": "1D", "count": 3},
          "the bar shape still matches the one mapped in market-research-pipeline"),
)


def describe_shape(value: Any, indent: str = "  ", depth: int = 0, max_depth: int = 3) -> list[str]:
    """Keys, types and list lengths — the structure, not the numbers."""
    lines: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            label = f"{indent * (depth + 1)}{key}: {_kind(inner)}"
            lines.append(label)
            if depth + 1 < max_depth and isinstance(inner, (dict, list)):
                lines.extend(describe_shape(inner, indent, depth + 1, max_depth))
    elif isinstance(value, list) and value:
        first = value[0]
        lines.append(f"{indent * (depth + 1)}[0]: {_kind(first)}")
        if depth + 1 < max_depth and isinstance(first, (dict, list)):
            lines.extend(describe_shape(first, indent, depth + 1, max_depth))
    return lines


def _kind(value: Any) -> str:
    if isinstance(value, dict):
        return f"object ({len(value)} keys)"
    if isinstance(value, list):
        return f"list ({len(value)} items)"
    if isinstance(value, str):
        return f"string ({len(value)} chars)"
    return type(value).__name__


def run_probes(client: Any, out_dir: Path, *, delays: tuple[float, ...],
               probes: tuple[Probe, ...] = PROBES) -> list[dict[str, Any]]:
    """Run every probe in one MCP session; a failed probe is recorded, not fatal."""

    async def work(session) -> list[dict[str, Any]]:
        report = []
        for probe in probes:
            entry: dict[str, Any] = {"name": probe.name, "tool": probe.tool,
                                     "arguments": probe.arguments, "why": probe.why}
            try:
                payload = await fetch_in_session(session, probe.tool, probe.arguments,
                                                 delays=delays)
            except RateLimited as exc:
                entry.update(status="rate_limited", error=str(exc))
            except ProviderError as exc:
                entry.update(status="failed", error=str(exc))
            else:
                entry.update(status="ok", saved=save_payload(out_dir, probe.name, payload),
                             shape=describe_shape(payload))
            log.info("probe %-20s %s", probe.name, entry["status"])
            report.append(entry)
        return report

    report = client.with_session(work)
    save_payload(out_dir, "summary", report)
    return report


def print_report(report: list[dict[str, Any]], out_dir: Path) -> None:
    for entry in report:
        print(f"\n== {entry['name']}  [{entry['status']}]  {entry['tool']}")
        print(f"   why: {entry['why']}")
        print(f"   args: {json.dumps(entry['arguments'])}")
        if entry["status"] == "ok":
            print("\n".join(entry["shape"][:60]))
            if len(entry["shape"]) > 60:
                print(f"   ... {len(entry['shape']) - 60} more lines in {entry['saved']}")
        else:
            print(f"   {entry['error'][:300]}")
    ok = sum(e["status"] == "ok" for e in report)
    print(f"\n{ok}/{len(report)} probes answered. Payloads: {out_dir}\n")
