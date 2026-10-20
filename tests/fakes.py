"""Offline stand-ins for TradingView. Tool names, argument names and payload shapes
copy the live server; every value is synthetic."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from tascreen.market_hours import is_trading_day

LIMITED = {"success": False,
           "error": "tradingview api: https://scanner.tradingview.com/america/scan: 429: "}


def result(payload):
    return SimpleNamespace(is_error=False, structured_content=payload,
                           content=[SimpleNamespace(text=json.dumps(payload))])


class FakeClient:
    """TradingViewMCP's with_session() over a fake session."""

    def __init__(self, session):
        self.session = session
        self.sessions_opened = 0

    def with_session(self, work):
        self.sessions_opened += 1
        return asyncio.run(work(self.session))


# -------------------------------------------------------------------- screener
def stock_row(i: int, market_cap: float, exchange: str = "NASDAQ", **extra) -> dict:
    """A screener row with the live key set (synthetic values)."""
    return {
        "symbol": f"{exchange}:S{i:04d}", "name": f"S{i:04d}", "description": f"Synthetic {i}",
        "type": "stock", "subtype": "common", "currency": "USD", "sector": "Synthetic",
        "industry": "Testing", "market_cap_basic": market_cap, "close": 10.0 + i,
        "volume": 1000 + i, "average_volume_10d_calc": 900.0 + i, "RSI": 50.0,
        "EMA50": 10.0, "EMA200": 9.0, "Recommend.All": 0.1, "earnings_release_next_date": None,
        "BB.lower": 1.0, "BB.upper": 2.0, "Perf.W": None,
        "change": 1.5, "change_abs": 0.15, "relative_volume_10d_calc": 1.2, **extra,
    }


class FakeScreener:
    """Applies `filters.market_cap_basic` as an inclusive [min, max] range, sorts by
    market cap and caps the rows, like the live tool."""

    def __init__(self, rows, cap=1000, too_big_over=None):
        self.rows = rows
        self.cap = cap
        self.too_big_over = too_big_over   # rows above which the MCP server refuses the size
        self.calls = []
        self.rate_limited = 0          # answer 429 to this many calls first

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.rate_limited:
            self.rate_limited -= 1
            return result(LIMITED)
        assert name == "mcp-tv-run-screener"
        lo, hi = args["filters"]["market_cap_basic"]

        def cap(r):     # "_filter_cap": the server's own value when a test hides the column
            return r.get("market_cap_basic", r.get("_filter_cap"))

        match = [r for r in self.rows
                 if (lo is None or cap(r) >= lo) and (hi is None or cap(r) <= hi)]
        match.sort(key=cap, reverse=True)
        limit = min(args["limit"], self.cap)
        if self.too_big_over is not None and min(limit, len(match)) > self.too_big_over:
            text = "Tool invocation failed: Result size 1234567 exceeds limit of 1000000 bytes"
            return SimpleNamespace(is_error=True, structured_content=None,
                                   content=[SimpleNamespace(text=text)])
        return result({"success": True, "data": {"rows": match[:limit], "totalCount": len(match)}})


# ------------------------------------------------------------------------ bars
def trading_days(end: date, count: int) -> list[date]:
    days, day = [], end
    while len(days) < count:
        if is_trading_day(day):
            days.append(day)
        day -= timedelta(days=1)
    return days[::-1]


def session_open_ts(day: date) -> int:
    return int(datetime.combine(day, time(13, 30), tzinfo=timezone.utc).timestamp())


class FakeOhlcv:
    """get-ohlcv over a synthetic price path per symbol, ending on `last_day`.
    `scale` rescales all history (what a split does to split-adjusted prices)."""

    def __init__(self, last_day: date, days: int = 800):
        self.last_day = last_day
        self.days = days
        self.scale: dict[str, float] = {}
        self.missing: set[str] = set()
        self.gone: set[str] = set()          # TradingView's answer for a symbol it no longer knows
        self.calls = []

    def bars_for(self, symbol: str) -> list[dict]:
        k = self.scale.get(symbol, 1.0)
        seed = sum(map(ord, symbol)) % 50
        out = []
        for i, day in enumerate(trading_days(self.last_day, self.days)):
            c = (50.0 + seed + (day.toordinal() % 20)) * k      # same day -> same price
            out.append({"t": session_open_ts(day), "o": c - 0.5 * k, "h": c + 1 * k,
                        "l": c - 1 * k, "c": c, "v": 1000 + i})
        return out

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        assert name == "mcp-tv-get-ohlcv" and args["interval"] == "1D"
        symbol = args["symbol"]
        if symbol in self.missing:
            return result({"success": False, "error": f"no data for {symbol}"})
        if symbol in self.gone:
            return result({"success": False, "error": "failed to fetch bars: tvws: symbol_error "
                                                      "[cs_test sds_sym_1 invalid symbol]"})
        bars = self.bars_for(symbol)[-args["count"]:]
        return result({"success": True, "symbol": symbol, "interval": "1D",
                       "count": len(bars), "bars": bars})


class FakeBatch(FakeOhlcv):
    """FakeOhlcv that also answers get-symbol-data-batch the way the live server does
    ({"data": {symbol: {column: value}}, "missing": [...]}), from the same synthetic bars.
    `wrong` symbols get a high that disagrees with their daily bar; after `quota_after`
    calls every call answers TradingView's daily-quota error."""

    def __init__(self, last_day: date, days: int = 800):
        super().__init__(last_day, days)
        self.wrong: set[str] = set()
        self.quota_after: int | None = None

    async def call_tool(self, name, args):
        if self.quota_after is not None and len(self.calls) >= self.quota_after:
            self.calls.append((name, args))
            return SimpleNamespace(is_error=True, structured_content=None, content=[SimpleNamespace(
                text="tool_pre_invoke blocked by plugin ToolQuotaPlugin: TOOL_QUOTA_EXCEEDED - Tool call quota "
                     "exceeded (Daily tool-call quota for this user is exhausted. It resets at 00:00 UTC, in "
                     "about 3h 0m; do not retry before then.)")])
        if name != "mcp-tv-get-symbol-data-batch":
            return await super().call_tool(name, args)
        self.calls.append((name, args))
        data, missing = {}, []
        for symbol in args["symbols"]:
            if symbol in self.missing:
                missing.append(symbol)
                continue
            prev, bar = self.bars_for(symbol)[-2:]
            data[symbol] = {"close": bar["c"], "high": bar["h"] + (1.0 if symbol in self.wrong else 0.0),
                            "low": bar["l"], "volume": bar["v"], "change_abs": bar["c"] - prev["c"],
                            "change_from_open_abs": bar["c"] - bar["o"]}
        return result({"success": True, "data": data, "missing": missing})
