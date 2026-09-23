"""Live quotes: the last price of every stock, refreshed during the session.

TradingView's screener returns the whole $1B+ list in market-cap bands (the same
fetch as the universe, with its completeness checks; see universe.py). Each row
carries the last price (`close`), the change since the previous close (`change`,
in percent: checked live against `change_abs`), the day's `volume` and
`relative_volume_10d_calc`. Prices are delayed (TradingView: 15 minutes or more).

A quote is not a close. Patterns are confirmed only by a session's close, in the
nightly scan. What quotes add is a *crossing*: a chart pattern still forming whose
breakout level for the next session (`trigger_up` / `trigger_down`, computed by
the scan) the live price has passed. It is shown as not final until the close.
"""
from __future__ import annotations

import math
import time
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any

import pandas as pd

from .config import UniverseSettings
from .contracts import QUOTES
from .fields import pick
from .market_hours import next_sessions
from .tv.data import SCREENER_TOOL
from .universe import _fetch_once, universe_row


def quote_row(row: dict[str, Any], context: str) -> dict[str, Any]:
    record = universe_row(row, context)

    def number(key: str) -> float:
        value = pick(row, [key], context=context, allow_null=True)
        return math.nan if value is None else float(value)

    record.update(price=record["close"], change_pct=number("change"),
                  change_abs=number("change_abs"), rel_volume=number("relative_volume_10d_calc"))
    return record


async def fetch_quotes(session, cfg: UniverseSettings, *, delays,
                       now: datetime | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Every stock's last price. A stock crossing a band edge mid-fetch can be missed;
    for quotes that is recorded (`complete`, `missing`) rather than fatal."""
    started = time.monotonic()
    records, info = await _fetch_once(session, cfg, delays, row=quote_row,
                                      context=f"{SCREENER_TOOL} quotes")
    frame = pd.DataFrame(records)
    keep = ~frame["exchange"].isin(cfg.drop_exchanges) & ~frame["subtype"].isin(cfg.drop_subtypes)
    quotes = frame.loc[keep, list(QUOTES.columns)].reset_index(drop=True)
    summary = {
        "fetched_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "rows": int(len(quotes)),
        "calls": info["calls"],
        "complete": len(records) == info["total"],
        "missing": max(0, info["total"] - len(records)),
        "seconds": round(time.monotonic() - started, 1),
    }
    return QUOTES.validate(quotes), summary


@lru_cache(maxsize=512)
def _next_session(day: date) -> date:
    return next_sessions(day, 1)[0]


def crossings(stocks: pd.DataFrame, detections: pd.DataFrame, prices: dict[str, float],
              quote_session: date) -> pd.DataFrame:
    """Forming chart patterns whose next-session breakout level the live price has passed.

    Only stocks whose last bar is the session right before `quote_session` count:
    the trigger levels were computed for exactly that next session."""
    columns = ["symbol", "pattern", "direction", "level", "price"]
    if detections.empty or "trigger_up" not in detections.columns:
        return pd.DataFrame(columns=columns)
    last = dict(zip(stocks["symbol"], stocks["last_date"]))
    forming = detections.loc[(detections["status"] == "forming") & (detections["family"] == "chart")]
    rows = []
    for d in forming.itertuples(index=False):
        price = prices.get(d.symbol)
        last_day = last.get(d.symbol)
        if price is None or not math.isfinite(price) or not last_day:
            continue
        if _next_session(date.fromisoformat(last_day)) != quote_session:
            continue
        if math.isfinite(d.trigger_up) and price > d.trigger_up:
            rows.append((d.symbol, d.pattern, "bullish", d.trigger_up, price))
        elif math.isfinite(d.trigger_down) and price < d.trigger_down:
            rows.append((d.symbol, d.pattern, "bearish", d.trigger_down, price))
    return pd.DataFrame(rows, columns=columns)
