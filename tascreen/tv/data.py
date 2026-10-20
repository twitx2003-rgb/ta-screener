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
- Since 2026-10-19 the server keeps a tool-call quota per account (all clients
  together): about 1000 calls a UTC day (measured: about 998 calls from 00:00 UTC,
  then "Daily tool-call quota for this user is exhausted. It resets at 00:00 UTC ...;
  do not retry before then"), and a per-minute one ("... exhausted for the current
  minute. Wait about 1 seconds"). Both come as a tool error with TOOL_QUOTA_EXCEEDED.
  The daily one is QuotaExhausted, and no call is made after it until 00:00 UTC; the
  per-minute one is RateLimited with the wait it names.

A payload that does not match a mapped shape is saved under logs/ and raised as
ShapeNotMapped — never read by guessing field names.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ..contracts import BARS, assert_ohlcv_sane, canonical_timestamps
from ..errors import ProviderError
from ..fields import pick

log = logging.getLogger(__name__)

OHLCV_TOOL = "mcp-tv-get-ohlcv"
NEWS_TOOL = "mcp-tv-get-news"      # {symbol, lang, limit, offset}; Hebrew returned none for NVDA
SCREENER_TOOL = "mcp-tv-run-screener"
COLUMNS_TOOL = "mcp-tv-get-screener-columns"
RATE_LIMIT_DELAYS = (5.0, 15.0, 45.0)
TOO_MANY = re.compile(r"(?<!\d)429(?!\d)")    # TradingView's 429, not a number containing it


class ToolFailed(ProviderError):
    """The tool ran and reported failure in its own payload."""


class RateLimited(ToolFailed):
    """The failure is a 429 from TradingView's backend; worth retrying. `retry_after` is
    the wait the server asked for (an HTTP 429 from the MCP endpoint: 60 s), else 0."""

    retry_after: float = 0.0


class QuotaExhausted(ToolFailed):
    """TradingView's daily tool-call quota for the account is used up (it resets at
    00:00 UTC). Not worth a retry before then: nothing is called until `quota_back()`."""


QUOTA = "TOOL_QUOTA_EXCEEDED"
DAILY_QUOTA = re.compile(r"daily tool-call quota", re.IGNORECASE)
QUOTA_WAIT = re.compile(r"wait about (\d+) seconds?", re.IGNORECASE)
_quota_back: datetime | None = None      # when the spent daily quota comes back (UTC)


def quota_back(now: datetime | None = None) -> datetime | None:
    """When TradingView's daily quota comes back, if this process found it spent; else None."""
    global _quota_back
    if _quota_back is not None and (now or datetime.now(timezone.utc)) >= _quota_back:
        _quota_back = None
    return _quota_back


def quota_spent(back: datetime | None) -> None:
    """Remember (or, with None, forget) that the daily quota is spent until `back`."""
    global _quota_back
    _quota_back = back


def _quota_error(tool: str, text: str) -> ProviderError | None:
    """The typed error for a quota answer, or None for any other text."""
    if QUOTA not in text:
        return None
    if DAILY_QUOTA.search(text):
        now = datetime.now(timezone.utc)
        quota_spent(datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), timezone.utc))
        return QuotaExhausted(f"{tool}: TradingView's daily tool-call quota is spent (back at 00:00 UTC)")
    wait = QUOTA_WAIT.search(text)
    error = RateLimited(f"{tool}: TradingView's per-minute tool-call quota is spent")
    error.retry_after = float(wait.group(1)) + 1 if wait else 5.0
    return error


def _refuse_while_spent(tool: str) -> None:
    back = quota_back()
    if back is not None:
        raise QuotaExhausted(f"{tool}: not called, TradingView's daily quota is spent until "
                             f"{back:%H:%M} UTC")


class ShapeNotMapped(ProviderError):
    """The tool answered, but not in the format mapped here (a public beta can
    change it). Raised instead of guessing; the payload is saved for a look."""


def tool_payload(result: Any, tool: str) -> dict[str, Any]:
    """The JSON object a tool returned, or a typed failure.

    Prefers `structured_content`; falls back to the text blocks when they are
    JSON. Refuses anything that is not an object, and any payload that says
    `success: false`. A 429 is RateLimited in both shapes: the payload's `error`, or (since
    2026-10-07, when the nightly update failed on it) an MCP-level error whose text holds
    the same JSON.
    """
    if result.is_error:
        text = _text(result)
        raise _quota_error(tool, text) or \
            (RateLimited if TOO_MANY.search(text) else ToolFailed)(f"{tool}: tool error: {text[:300]}")

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
        quota = _quota_error(tool, error)
        if quota is not None:
            raise quota
        kind = RateLimited if TOO_MANY.search(error) else ToolFailed
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
            _refuse_while_spent(tool)
            try:
                return tool_payload(self.client.call_tool(tool, arguments), tool)
            except RateLimited as exc:
                if delay is None:
                    raise RateLimited(f"{exc} (still rate limited after "
                                      f"{len(self.delays) + 1} attempts)") from None
                delay = max(delay, exc.retry_after)
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
        _refuse_while_spent(tool)
        try:
            return tool_payload(await session.call_tool(tool, arguments), tool)
        except RateLimited as exc:
            if delay is None:
                raise RateLimited(f"{exc} (still rate limited after "
                                  f"{len(delays) + 1} attempts)") from None
            delay = max(delay, exc.retry_after)
            log.warning("%s rate limited by TradingView; retrying in %.0fs", tool, delay)
            await sleep(delay)
    raise AssertionError("unreachable")
