"""TradingView MCP tool results -> checked Python data.

`mcp_client.py` is the transport. This module turns tool results into data the
screener can trust, and is strict about it because the server is a public beta
whose output formats are undocumented.

What the live server showed (market-research-pipeline, 2026-09):

- A failed call is NOT flagged at the MCP level: `is_error` is False and the
  payload is `{"success": false, "error": "..."}`. Reading `is_error` alone
  would pass a failure off as data.
- The screener-backed tools returned
  `tradingview api: https://scanner.tradingview.com/...: 429` for hours at a
  time — a rate limit behind the MCP server. It is retried with backoff;
  anything else fails at once.
- `get-ohlcv` returns bars `{t, o, h, l, c, v}` oldest first, `t` in unix
  seconds at the session open, and the newest bar can be today's unfinished
  session.

A payload that does not match a mapped shape is saved under logs/ and raised as
ShapeNotMapped — never read by guessing field names.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ..contracts import BARS, assert_ohlcv_sane, canonical_timestamps
from ..errors import ProviderError
from ..fields import pick

log = logging.getLogger(__name__)

OHLCV_TOOL = "mcp-tv-get-ohlcv"
SCREENER_TOOL = "mcp-tv-run-screener"
COLUMNS_TOOL = "mcp-tv-get-screener-columns"
RATE_LIMIT_DELAYS = (5.0, 15.0, 45.0)


class ToolFailed(ProviderError):
    """The tool ran and reported failure in its own payload."""


class RateLimited(ToolFailed):
    """The failure is a 429 from TradingView's backend; worth retrying."""


class ShapeNotMapped(ProviderError):
    """The tool answered, but not in the format mapped here (a public beta can
    change it). Raised instead of guessing; the payload is saved for a look."""


def tool_payload(result: Any, tool: str) -> dict[str, Any]:
    """The JSON object a tool returned, or a typed failure.

    Prefers `structured_content`; falls back to the text blocks when they are
    JSON. Refuses anything that is not an object, and any payload that says
    `success: false`.
    """
    if result.is_error:
        raise ToolFailed(f"{tool}: tool error: {_text(result)[:300]}")

    payload = result.structured_content
    if payload is None:
        try:
            payload = json.loads(_text(result))
        except ValueError:
            raise ProviderError(f"{tool}: result is not JSON: {_text(result)[:300]!r}") from None
    if not isinstance(payload, dict):
        raise ProviderError(f"{tool}: expected a JSON object, got {type(payload).__name__}")

    if payload.get("success") is False:
        error = str(payload.get("error") or "(no error text)")
        kind = RateLimited if re.search(r"(?<!\d)429(?!\d)", error) else ToolFailed
        raise kind(f"{tool}: {error}")
    return payload


def _text(result: Any) -> str:
    return "\n".join(t for t in (getattr(b, "text", None) for b in result.content) if t)


def save_payload(dump_dir: Path | None, name: str, payload: Any) -> str:
    """Write a payload for a human to look at. Returns where it went."""
    if dump_dir is None:
        return "not saved"
    dump_dir.mkdir(parents=True, exist_ok=True)
    target = dump_dir / f"{name}.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                      encoding="utf-8")
    return str(target)


def bars_frame(payload: dict[str, Any], symbol: str) -> pd.DataFrame:
    """`get-ohlcv` payload -> frame in the BARS layout, oldest first."""
    context = f"{OHLCV_TOOL} {symbol}"
    returned = payload.get("symbol")
    if returned is not None and returned != symbol:
        raise ProviderError(f"{context}: asked for {symbol}, server answered for {returned}")

    bars = pick(payload, ["bars"], context=context)
    if not isinstance(bars, list):
        raise ProviderError(f"{context}: 'bars' is {type(bars).__name__}, expected a list")
    if not bars:
        raise ProviderError(f"{context}: no bars returned")

    rows = [{
        "timestamp": pd.Timestamp(pick(b, ["t"], context=context), unit="s", tz="UTC"),
        "symbol": symbol,
        "open": float(pick(b, ["o"], context=context)),
        "high": float(pick(b, ["h"], context=context)),
        "low": float(pick(b, ["l"], context=context)),
        "close": float(pick(b, ["c"], context=context)),
        "volume": float(pick(b, ["v"], context=context)),
    } for b in bars]
    df = pd.DataFrame(rows)
    df["timestamp"] = canonical_timestamps(df["timestamp"])

    # Oldest first is what the live server sends; verify instead of assuming.
    if not df["timestamp"].is_monotonic_increasing:
        raise ProviderError(f"{context}: bars are not in ascending time order")
    dates = df["timestamp"].dt.date
    if dates.duplicated().any():
        raise ProviderError(f"{context}: more than one bar for {dates[dates.duplicated()].iloc[0]}")
    return BARS.validate(assert_ohlcv_sane(df))


class TradingViewData:
    """Read-only data calls on top of a TradingViewMCP client, one session per call."""

    def __init__(self, client: Any, *, delays: tuple[float, ...] = RATE_LIMIT_DELAYS,
                 sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.delays = delays
        self.sleep = sleep

    def fetch(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool and return its checked payload, retrying only on 429."""
        for delay in (*self.delays, None):
            try:
                return tool_payload(self.client.call_tool(tool, arguments), tool)
            except RateLimited as exc:
                if delay is None:
                    raise RateLimited(f"{exc} (still rate limited after "
                                      f"{len(self.delays) + 1} attempts)") from None
                log.warning("%s rate limited by TradingView; retrying in %.0fs", tool, delay)
                self.sleep(delay)
        raise AssertionError("unreachable")

    def daily_bars(self, symbol: str, count: int) -> pd.DataFrame:
        payload = self.fetch(OHLCV_TOOL, {"symbol": symbol, "interval": "1D", "count": count})
        return bars_frame(payload, symbol)


async def fetch_in_session(session: Any, tool: str, arguments: dict[str, Any], *,
                           delays: tuple[float, ...] = RATE_LIMIT_DELAYS,
                           sleep: Callable[[float], Any] = asyncio.sleep) -> dict[str, Any]:
    """`TradingViewData.fetch` for an open session (many calls, one connection)."""
    for delay in (*delays, None):
        try:
            return tool_payload(await session.call_tool(tool, arguments), tool)
        except RateLimited as exc:
            if delay is None:
                raise RateLimited(f"{exc} (still rate limited after "
                                  f"{len(delays) + 1} attempts)") from None
            log.warning("%s rate limited by TradingView; retrying in %.0fs", tool, delay)
            await sleep(delay)
    raise AssertionError("unreachable")
