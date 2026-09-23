"""The universe: every US-listed stock above the market-cap floor, from TradingView's screener.

`run-screener` returns at most 1000 rows and has no offset, the MCP server
refuses answers over 1 MB (about 430 default rows), and more than $1B is about
4,000 "stock" rows (OTC included). So the list is fetched in market-cap bands.
The result is only accepted if it is provably complete:

- a band whose `totalCount` exceeds what it returned, or whose answer is over
  the size limit, is split in two and fetched again (never truncated);
- every row must sit inside the band it came from (proves the filter did what
  we asked, rather than assuming the semantics of `[min, max]`);
- after de-duplication (bands share their edges), the number of symbols must
  equal the `totalCount` of one unsplit query. Market caps move during the
  session, so a stock can cross a band edge between two calls; the whole fetch
  is retried once before giving up.

Live shape (2026-09-23): `{success, data: {rows: [...], totalCount}}`; rows carry
`symbol` ("EXCHANGE:TICKER"), `name` (ticker), `type`, `subtype`, `sector`,
`industry`, `market_cap_basic`, `close`, `volume`, `RSI`, `EMA50`, `EMA200`,
`Recommend.All`, `earnings_release_next_date`, ... There is no `exchange` key;
the exchange is the symbol's prefix. Any value may be null.

OTC symbols and preferred issues (`universe.drop_exchanges`, `drop_subtypes`)
are dropped only after the completeness check, which counts them.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

import pandas as pd

from .config import UniverseSettings
from .contracts import UNIVERSE
from .errors import ProviderError
from .fields import pick
from .tv.data import SCREENER_TOOL, ToolFailed, fetch_in_session

log = logging.getLogger(__name__)

MAX_SPLITS = 12          # a band this deep in splits means the data is not what we think
# The MCP server refuses results over 1,000,000 bytes (live: "Tool invocation
# failed: Result size 1067868 exceeds limit of 1000000 bytes"). The size counts
# more than the JSON payload (a 398-row band was ~0.46 MB of JSON and passed; a
# band under 500 rows failed), so row_cap alone cannot guarantee it: an answer
# that is too big splits its band, like one over the row cap.
_TOO_BIG = re.compile(r"Result size \d+ exceeds limit")


def screener_arguments(cfg: UniverseSettings, lo: float, hi: float | None, limit: int) -> dict:
    return {
        "market": cfg.market,
        "symbol_types": ["stock"],
        "filters": {"market_cap_basic": [lo, hi]},
        "sort_by": "market_cap_basic",
        "sort_order": "desc",
        "limit": limit,
    }


def parse_screener(payload: dict[str, Any], context: str) -> tuple[list[dict], int]:
    data = pick(payload, ["data"], context=context)
    rows = pick(data, ["rows"], context=context)
    total = pick(data, ["totalCount"], context=context)
    if not isinstance(rows, list):
        raise ProviderError(f"{context}: 'rows' is {type(rows).__name__}, expected a list")
    if not isinstance(total, int) or total < 0:
        raise ProviderError(f"{context}: 'totalCount' is {total!r}, expected a count")
    return rows, total


def universe_row(row: dict[str, Any], context: str) -> dict[str, Any]:
    """One screener row -> one UNIVERSE record. Missing keys fail; nulls are kept
    only where a gap is legitimate (a stock with no RSI yet, no earnings date)."""
    symbol = pick(row, ["symbol"], context=context)
    if not isinstance(symbol, str) or symbol.count(":") != 1:
        raise ProviderError(f"{context}: symbol {symbol!r} is not EXCHANGE:TICKER")
    kind = pick(row, ["type"], context=context)
    if kind != "stock":
        raise ProviderError(f"{context}: {symbol} has type {kind!r}; asked for stocks only")
    market_cap = pick(row, ["market_cap_basic"], context=context)

    def number(key: str) -> float:
        value = pick(row, [key], context=context, allow_null=True)
        return math.nan if value is None else float(value)

    return {
        "symbol": symbol,
        "exchange": symbol.split(":")[0],
        "ticker": pick(row, ["name"], context=context),
        "description": pick(row, ["description"], context=context, allow_null=True),
        "subtype": pick(row, ["subtype"], context=context, allow_null=True),
        "sector": pick(row, ["sector"], context=context, allow_null=True),
        "industry": pick(row, ["industry"], context=context, allow_null=True),
        "currency": pick(row, ["currency"], context=context, allow_null=True),
        "market_cap": float(market_cap),
        "close": number("close"),
        "volume": number("volume"),
        "avg_volume_10d": number("average_volume_10d_calc"),
        "tv_rsi": number("RSI"),
        "tv_ema50": number("EMA50"),
        "tv_ema200": number("EMA200"),
        "tv_rating": number("Recommend.All"),
        "next_earnings": pick(row, ["earnings_release_next_date"], context=context,
                              allow_null=True),
    }


def _split(lo: float, hi: float | None) -> float:
    return math.sqrt(lo * hi) if hi is not None else lo * 10


def _label(lo: float, hi: float | None) -> str:
    return f"[{lo / 1e9:g}B, {hi / 1e9:g}B]" if hi is not None else f"[{lo / 1e9:g}B, open]"


async def _fetch_once(session, cfg: UniverseSettings, delays) -> tuple[list[dict], dict]:
    context = f"{SCREENER_TOOL} universe"
    payload = await fetch_in_session(session, SCREENER_TOOL,
                                     screener_arguments(cfg, cfg.min_market_cap, None, 1),
                                     delays=delays)
    _, total = parse_screener(payload, context)

    edges = list(cfg.band_edges)
    queue: list[tuple[float, float | None, int]] = [
        (lo, hi, 0) for lo, hi in zip(edges, edges[1:] + [None])]
    records: dict[str, dict] = {}
    bands = []
    while queue:
        lo, hi, depth = queue.pop(0)
        where = f"{context} band {_label(lo, hi)}"
        try:
            payload = await fetch_in_session(session, SCREENER_TOOL,
                                             screener_arguments(cfg, lo, hi, cfg.row_cap),
                                             delays=delays)
        except ToolFailed as exc:
            if not _TOO_BIG.search(str(exc)):
                raise
            if depth >= MAX_SPLITS:
                raise ProviderError(f"{where}: answer still over the size limit after "
                                    f"{depth} splits") from None
            mid = _split(lo, hi)
            log.info("%s: answer over the server's size limit; splitting at %.4gB",
                     where, mid / 1e9)
            queue[:0] = [(lo, mid, depth + 1), (mid, hi, depth + 1)]
            continue
        rows, band_total = parse_screener(payload, where)
        if band_total > len(rows):
            if len(rows) < cfg.row_cap:
                raise ProviderError(f"{where}: returned {len(rows)} of {band_total} rows "
                                    f"although under the {cfg.row_cap}-row cap")
            if depth >= MAX_SPLITS:
                raise ProviderError(f"{where}: still over the row cap after {depth} splits")
            mid = _split(lo, hi)
            log.info("%s holds %d rows (> %d); splitting at %.4gB", where, band_total,
                     cfg.row_cap, mid / 1e9)
            queue[:0] = [(lo, mid, depth + 1), (mid, hi, depth + 1)]
            continue
        if band_total != len(rows):
            raise ProviderError(f"{where}: totalCount {band_total} but {len(rows)} rows")
        for row in rows:
            record = universe_row(row, where)
            cap = record["market_cap"]
            if cap < lo or (hi is not None and cap > hi):
                raise ProviderError(f"{where}: {record['symbol']} has market cap {cap:.4g}, "
                                    "outside the band it was returned for — the filter did "
                                    "not do what this code assumes")
            records[record["symbol"]] = record
        bands.append({"low": lo, "high": hi, "rows": len(rows)})

    return list(records.values()), {"total": total, "bands": bands}


async def fetch_universe(session, cfg: UniverseSettings, *, delays,
                         now: datetime | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """The complete universe, OTC dropped, with a summary of how it was fetched."""
    for attempt in (1, 2):
        records, info = await _fetch_once(session, cfg, delays)
        if len(records) == info["total"]:
            break
        message = (f"universe incomplete: {len(records)} distinct symbols across the bands, "
                   f"but the unsplit query counts {info['total']}")
        if attempt == 2:
            raise ProviderError(message + " (twice). Market caps may be moving across band "
                                "edges during the session; try again after the close.")
        log.warning("%s; fetching again", message)

    frame = pd.DataFrame(records, columns=list(UNIVERSE.columns))
    by_exchange = frame["exchange"].isin(cfg.drop_exchanges)
    by_subtype = frame["subtype"].isin(cfg.drop_subtypes) & ~by_exchange
    kept = frame[~(by_exchange | by_subtype)].sort_values(
        "market_cap", ascending=False).reset_index(drop=True)
    summary = {
        "fetched_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "min_market_cap": cfg.min_market_cap,
        "total_including_dropped": info["total"],
        "dropped": {**dict(Counter(frame.loc[by_exchange, "exchange"])),
                    **{f"subtype {k}": v for k, v in Counter(frame.loc[by_subtype, "subtype"]).items()}},
        "kept": int(len(kept)),
        "exchanges": dict(Counter(kept["exchange"])),
        "subtypes": dict(Counter(kept["subtype"].fillna("(none)"))),
        "bands": info["bands"],
    }
    return UNIVERSE.validate(kept), summary
