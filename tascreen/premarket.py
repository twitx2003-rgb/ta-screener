"""Big moves before the open (owner's request, 2026-09-28): three times in New York's
pre-market (07:30, 08:30, 09:15) the bot sends the universe's stocks moving most since the last
close, and flags any whose pre-market price is above a forming pattern's breakout line.
Nothing here is a breakout: a pattern is confirmed only by a session's close.

Fields (TradingView's screener, confirmed with a live call on 2026-09-28): the tool
takes `columns`, filters on any column and sorts by one. `premarket_close` is the last
pre-market price, `premarket_change` its % change and `premarket_change_abs` its change
in dollars, both from the previous regular close, which is `close` while the new session
has not opened. **Before a day's pre-market starts, the fields still hold the last
pre-market's values** (live, Monday 01:55 New York: Friday's). A row is used only when
close + premarket_change_abs == premarket_close, which holds for today's values and not
for stale ones (their base is the close before `close`).
"""
from __future__ import annotations

import html
import math
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .alerts import _link
from .config import UniverseSettings
from .fields import pick
from .market_hours import next_open
from .tv.data import SCREENER_TOOL, fetch_in_session
from .universe import parse_screener

SLOTS = (time(7, 30), time(8, 30), time(9, 15))    # New York; the open is 09:30
SLOT_WINDOW_MIN = 40         # a late GitHub start still counts for its slot, this long
COLUMNS = ["close", "premarket_close", "premarket_change", "premarket_change_abs",
           "premarket_volume", "market_cap_basic", "description"]
PER_SIDE = 8                 # movers shown up and down


def slot_due(now: datetime, market_tz: str, done: dict[str, Any]) -> tuple[date | None, str]:
    """(the session day, the slot "HH:MM") when a pre-market report is due now, else
    (None, why not). The next open must be today in New York and at most two hours
    after the slot; each slot is sent once (`done` holds the ones sent)."""
    tz = ZoneInfo(market_tz)
    local = now.astimezone(tz)
    opens = next_open(now, market_tz=market_tz).astimezone(tz)
    if opens.date() != local.date() or local >= opens:
        return None, "no pre-market now"
    for slot in reversed(SLOTS):
        start = datetime.combine(local.date(), slot, tz)
        if start <= local < min(opens, start + timedelta(minutes=SLOT_WINDOW_MIN)):
            label = slot.strftime("%H:%M")
            return (None, "already sent") if label in done else (local.date(), label)
    return None, "between slots"


def arguments(cfg: UniverseSettings, *, up: bool, min_pct: float, min_volume: float, limit: int) -> dict:
    return {
        "market": cfg.market,
        "symbol_types": ["stock"],
        "filters": {"market_cap_basic": [cfg.min_market_cap, None],
                    "premarket_change": [min_pct, None] if up else [None, -min_pct],
                    "premarket_volume": [min_volume, None]},
        "sort_by": "premarket_change",
        "sort_order": "desc" if up else "asc",
        "limit": limit,
        "columns": COLUMNS,
    }


def mover(row: dict[str, Any], context: str, drop_exchanges: tuple[str, ...] | list[str]) -> dict[str, Any] | None:
    """One screener row -> a mover, or None (OTC, a missing value, or yesterday's figures)."""
    symbol = str(pick(row, ["symbol"], context=context))
    if symbol.split(":")[0] in drop_exchanges:
        return None
    values = {}
    for key in ("close", "premarket_close", "premarket_change", "premarket_change_abs", "premarket_volume"):
        value = pick(row, [key], context=context, allow_null=True)
        if value is None or not math.isfinite(float(value)):
            return None
        values[key] = float(value)
    if not fresh(values["close"], values["premarket_change_abs"], values["premarket_close"]):
        return None
    return {"symbol": symbol, "name": str(pick(row, ["description"], context=context, allow_null=True) or ""),
            "prev_close": values["close"], "price": values["premarket_close"],
            "change_pct": values["premarket_change"], "volume": values["premarket_volume"]}


def fresh(close: float, change_abs: float, price: float) -> bool:
    return abs(close + change_abs - price) <= max(0.02, 0.003 * abs(price))


async def fetch_movers(session, cfg: UniverseSettings, delays, *, min_pct: float,
                       min_volume: float, limit: int = 40) -> dict[str, Any]:
    """The biggest fresh movers up and down (two screener calls)."""
    out: dict[str, Any] = {"up": [], "down": [], "stale": 0}
    for up in (True, False):
        context = f"{SCREENER_TOOL} premarket {'up' if up else 'down'}"
        payload = await fetch_in_session(session, SCREENER_TOOL,
                                         arguments(cfg, up=up, min_pct=min_pct, min_volume=min_volume,
                                                   limit=limit), delays=delays)
        rows, _ = parse_screener(payload, context)
        for row in rows:
            m = mover(row, context, cfg.drop_exchanges)
            if m is None:
                out["stale"] += 1
                continue
            if (m["change_pct"] >= min_pct) if up else (m["change_pct"] <= -min_pct):
                out["up" if up else "down"].append(m)
    for side in ("up", "down"):
        out[side] = sorted(out[side], key=lambda m: -abs(m["change_pct"]))[:PER_SIDE]
    return out


def _price(x: float) -> str:
    return f"{x:,.2f}"


def message(slot: str, movers: dict[str, Any], crossings: list[dict[str, Any]], min_pct: float, *,
            index_line: str = "", why: str | None = None) -> str:
    """The report in Telegram HTML; with the index funds' line and the market explainer's
    sentences (tascreen/explain.py) when they are there."""
    lines = [f"🌅 <b>לפני הפתיחה</b> ({slot} בניו יורק) · מחירי טרום מסחר, לא סופיים"]
    if index_line:
        lines.append(index_line)
    if why:
        lines.append(f"🧭 <b>למה:</b> {html.escape(why)}")
    if crossings:
        lines.append("\n⚡ <b>מעל קו פריצה של תבנית</b> (פריצה רק אם תיסגר מעליו):")
        for c in crossings:
            lines.append(f"{_link(c['symbol'])} · {html.escape(str(c['name']))} · קו {_price(c['line'])} · "
                         f"עכשיו {_price(c['price'])}")
    for side, title in (("up", "🟢 <b>עולות</b>"), ("down", "🔴 <b>יורדות</b>")):
        if movers[side]:
            lines.append(f"\n{title}:")
            for m in movers[side]:
                name = f" · {html.escape(m['name'][:28])}" if m["name"] else ""
                lines.append(f"{_link(m['symbol'])}{name} · {m['change_pct']:+.1f}% · {_price(m['price'])}")
    if not movers["up"] and not movers["down"] and not crossings:
        lines.append(f"אין מניות שזזות יותר מ-{min_pct:g}% לפני הפתיחה.")
    return "\n".join(lines)

