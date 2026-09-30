"""The owner's own TradingView watchlist, followed closely (owner, 2026-09-30): its stocks are
priced every pass of the live watch, any of their chart patterns crossing its line is an
alert, a move of `move_pct` from the last close (and each further `move_pct`) is an
alert, and X news about them is sent too. All of it goes to the owner's PRIVATE chat
(owner's choice: the group must not learn the holdings).

The list is read from TradingView once a trading day (the live watch starts it) and kept
in the private state repo (data/watchlist.json); config.yaml holds only its opaque id.
Symbols never reach a public log: counts only.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from .fields import pick
from .tv.data import fetch_in_session

TOOL = "mcp-watchlist-get-watchlist"      # {"watchlist": {"name", "symbols": ["NASDAQ:NVDA", ...]}} (live, 2026-09-30)


def symbols_of(payload: dict) -> tuple[str, list[str]]:
    """(the list's name, its symbols): section headers ("###...") and odd entries dropped."""
    wl = pick(payload, ["watchlist"], context=TOOL)
    name = str(pick(wl, ["name"], context=TOOL))
    raw = pick(wl, ["symbols"], context=TOOL) or []
    return name, [s for s in raw if isinstance(s, str) and ":" in s and not s.startswith("###")]


async def fetch(session, watchlist_id: str, delays) -> tuple[str, list[str]]:
    payload = await fetch_in_session(session, TOOL, {"watchlist_id": str(watchlist_id)}, delays=delays)
    return symbols_of(payload)


def path(root: Path) -> Path:
    return root / "watchlist.json"


def read(root: Path) -> dict[str, Any]:
    p = path(root)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def write(root: Path, data: dict[str, Any]) -> None:
    path(root).parent.mkdir(parents=True, exist_ok=True)
    path(root).write_text(json.dumps(data), encoding="utf-8")


def tickers(symbols: list[str]) -> list[str]:
    return [s.split(":")[-1] for s in symbols]


def mentions(text: str, names: list[str]) -> list[str]:
    """The tickers a text names: "$NVDA", or NVDA as a word in capitals."""
    found = []
    for t in names:
        if re.search(rf"(?<![A-Za-z0-9])\$?{re.escape(t)}(?![A-Za-z0-9])", text or ""):
            found.append(t)
    return found


def new_moves(prices: dict[str, float], closes: dict[str, float], sent: dict[str, dict],
              step: float) -> list[dict[str, Any]]:
    """The holdings that moved another `step` % from the last close since the last alert:
    each at most once per level and direction a session (4%, then 8%, ...)."""
    out = []
    for symbol, price in prices.items():
        close = closes.get(symbol)
        if not close:
            continue
        change = (price / close - 1) * 100
        level = int(abs(change) // step) * step
        side = "up" if change > 0 else "down"
        if level >= step and level > float((sent.get(symbol) or {}).get(side, 0)):
            out.append({"symbol": symbol, "price": price, "close": close, "change": change,
                        "side": side, "level": level})
    out.sort(key=lambda m: -abs(m["change"]))
    return out


def move_message(moves: list[dict[str, Any]]) -> str:
    lines = ["📌 <b>תנועה חריגה ברשימה שלך</b>"]
    for m in moves:
        arrow = "▲" if m["side"] == "up" else "▼"
        lines.append(f"{html.escape(m['symbol'].split(':')[-1])} {arrow} {m['change']:+.1f}% · "
                     f"{m['price']:,.2f} (סגירה קודמת {m['close']:,.2f})")
    return "\n".join(lines)


CROSSING_HEAD = "📌 <b>פריצה במניה מהרשימה שלך</b>"
NEWS_HEAD = "📌 <b>חדשות על מניה מהרשימה שלך</b>"
