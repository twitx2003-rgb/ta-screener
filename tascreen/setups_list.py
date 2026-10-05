"""The bot's own TradingView watchlist of good setups and fresh breakouts (owner, 2026-10-02:
"a watchlist of the interesting stocks with a good setup or just after a breakout, that
refreshes itself and does not keep stocks or setups that did not work out").

Once per session, after the evening report:
1. In: the research team's picks of the session (data/research/picks.json), a forming
   pattern as a "setup", a confirmed breakout as a "breakout".
2. Review of every entry against the session's scan and bars (our rules, not Bulkowski's):
   - a setup that broke out becomes a breakout;
   - a setup leaves when its close drifts more than `max_gap_pct` below the line, when the
     scan has not found its pattern for two sessions, or after `setup_sessions` sessions
     without a breakout;
   - a breakout leaves when a session closes below its breakout line (the same rule as
     alerts.bullish_breakouts), when a high reaches the target (it worked: the move is
     done), or after `breakout_sessions` sessions (no longer fresh);
   - the oldest leave first when the list holds more than `max_size`;
   - a stock the scan no longer covers leaves (it fell under the universe's market-cap or
     volume floor; owner, 2026-10-05: only stocks with a relatively large volume).
3. TradingView: missing symbols are added, and symbols this bot added that left are removed;
   a symbol the owner put there by hand stays. Only this list's id can be edited
   (tv.mcp_client.OwnListSession).
4. What changed goes to the owner's PRIVATE chat.

The entries are kept in the private state repo (data/setups_list.json); public logs get
counts only.
"""
from __future__ import annotations

import html
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .fields import pick
from .patterns.levels import day_of, invalidation
from .store import _write_json
from .tv.data import fetch_in_session
from .web.data import ScanView, detection_record

NAME = "הבוט: סטאפים ופריצות"        # the list's name in TradingView, given when it was created
GET = "mcp-watchlist-get-watchlist"   # {"watchlist": {"id", "name", "symbols": [...]}} (live, 2026-10-02)
ADD = "mcp-watchlist-add-to-watchlist"
REMOVE = "mcp-watchlist-remove-from-watchlist"
MISSING_LIMIT = 2                     # sessions in a row without the pattern in the scan

REASONS = {
    "drifted": "התרחקה מקו הפריצה",
    "vanished": "התבנית כבר לא מופיעה בסריקה",
    "no_breakout": "לא פרצה בזמן",
    "fell_back": "נסגרה מתחת לקו הפריצה",
    "target": "הגיעה ליעד ✅",
    "stale": "הפריצה כבר לא טרייה",
    "room": "פינוי מקום לחדשות",
    "unfollowed": "כבר לא ברשימת המניות של הבוט (מחזור מסחר או שווי)",
}


# ------------------------------------------------------------------ state
def path(data_dir: Path) -> Path:
    return data_dir / "setups_list.json"


def read(data_dir: Path) -> dict[str, Any]:
    try:
        state = json.loads(path(data_dir).read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def write(data_dir: Path, state: dict[str, Any]) -> None:
    _write_json(path(data_dir), state)


# ------------------------------------------------------------------ the scan's facts
def _finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _row(view: ScanView, symbol: str, pattern: str, status: str) -> dict[str, Any] | None:
    """The scan's newest bullish row of this symbol's pattern with this status."""
    d = view.detections
    rows = d.loc[(d["symbol"] == symbol) & (d["pattern"] == pattern) & (d["status"] == status)
                 & (d["family"] == "chart") & d["direction"].isin(["bullish", "either"])]
    if not len(rows):
        return None
    if "age" in rows.columns:
        rows = rows.sort_values("age", kind="stable")
    return rows.iloc[0].to_dict()


def _close(view: ScanView, symbol: str) -> float | None:
    """The session's close, only when the stock's last bar is the scan's session."""
    s = view.stocks
    rows = s.loc[s["symbol"] == symbol]
    if not len(rows):
        return None
    row = rows.iloc[0]
    if str(row.get("last_date", ""))[:10] != view.day.isoformat() or not _finite(row.get("close")):
        return None
    return float(row["close"])


def _after(bars: pd.DataFrame | None, day: str) -> pd.DataFrame:
    """The bars of the sessions after `day`, up to the newest."""
    if bars is None or bars.empty or not day:
        return pd.DataFrame(columns=["high"])
    since = date.fromisoformat(day[:10])
    days = bars["timestamp"].map(day_of)
    return bars.loc[days > since]


def _as_breakout(entry: dict[str, Any], row: dict[str, Any], bars_of: Callable) -> dict[str, Any]:
    return {**entry, "kind": "breakout", "line": float(row["breakout_price"]),
            "target": float(row["target"]) if _finite(row.get("target")) else None,
            "invalidation": _num(invalidation(detection_record(row), bars_of(entry["symbol"]))),
            "breakout_day": str(day_of(row.get("event_day")) or ""), "missing": 0}


def _num(x: Any) -> float | None:
    return float(x) if _finite(x) else None


# ------------------------------------------------------------------ in and out
def new_entries(picks: list[dict], view: ScanView, bars_of: Callable, have: set[str]) -> list[dict]:
    """The session's research picks not on the list yet, with their line and target."""
    out = []
    for p in picks:
        symbol, pattern = p.get("symbol"), p.get("pattern")
        if not symbol or symbol in have or p.get("day") != view.day.isoformat():
            continue
        base = {"symbol": symbol, "pattern": pattern, "added": view.day.isoformat(), "missing": 0}
        breakout = _row(view, symbol, pattern, "breakout")
        forming = _row(view, symbol, pattern, "forming")
        if p.get("kind") == "breakout" and breakout is not None:
            entry = _as_breakout({**base, "name": breakout.get("name_he") or pattern}, breakout, bars_of)
        elif forming is not None and _finite(forming.get("trigger_up")):
            entry = {**base, "name": forming.get("name_he") or pattern, "kind": "setup",
                     "line": float(forming["trigger_up"]), "target": _num(forming.get("target")),
                     "breakout_day": ""}
        else:
            continue                                    # the pick's pattern is not in the scan
        out.append(entry)
        have = have | {symbol}
    return out


def review(entries: list[dict], view: ScanView, bars_of: Callable, *, max_gap_pct: float,
           setup_sessions: int, breakout_sessions: int) -> tuple[list[dict], list[dict]]:
    """(entries kept, events): each event is {"symbol", "name", "what": "broke_out" |
    "removed", "reason"}. An entry without a close of the session is kept as it is."""
    kept, events = [], []

    def drop(e: dict, reason: str) -> None:
        events.append({"symbol": e["symbol"], "name": e.get("name", ""), "what": "removed", "reason": reason})

    followed = set(view.stocks["symbol"])
    for e in entries:
        e = dict(e)
        if e["symbol"] not in followed:             # left the universe (e.g. the volume floor)
            drop(e, "unfollowed")
            continue
        close = _close(view, e["symbol"])
        if close is None:
            kept.append(e)
            continue
        bars = bars_of(e["symbol"])
        if e["kind"] == "setup":
            broke = _row(view, e["symbol"], e["pattern"], "breakout")
            broke_day = day_of(broke.get("event_day")) if broke is not None else None
            if broke is not None and broke_day and broke_day >= date.fromisoformat(e["added"]) \
                    and _finite(broke.get("breakout_price")):
                e = _as_breakout(e, broke, bars_of)
                events.append({"symbol": e["symbol"], "name": e.get("name", ""), "what": "broke_out", "reason": ""})
            else:
                forming = _row(view, e["symbol"], e["pattern"], "forming")
                if forming is None:
                    e["missing"] = int(e.get("missing") or 0) + 1
                    if e["missing"] >= MISSING_LIMIT:
                        drop(e, "vanished")
                        continue
                else:
                    e["missing"] = 0
                    if _finite(forming.get("trigger_up")):
                        e["line"] = float(forming["trigger_up"])
                    if (e["line"] / close - 1) * 100 > max_gap_pct:
                        drop(e, "drifted")
                        continue
                if len(_after(bars, e["added"])) > setup_sessions:
                    drop(e, "no_breakout")
                    continue
                kept.append(e)
                continue
        # a breakout (or a setup that just became one)
        after = _after(bars, e.get("breakout_day", ""))
        if close < float(e["line"]):
            drop(e, "fell_back")
        elif _finite(e.get("target")) and len(after) and float(after["high"].max()) >= float(e["target"]):
            drop(e, "target")
        elif len(after) > breakout_sessions:
            drop(e, "stale")
        else:
            kept.append(e)
    return kept, events


def cap(entries: list[dict], max_size: int) -> tuple[list[dict], list[dict]]:
    """The newest `max_size` entries; the oldest leave first."""
    if len(entries) <= max_size:
        return entries, []
    ordered = sorted(entries, key=lambda e: (e["added"], e["symbol"]))
    out = ordered[: len(entries) - max_size]
    gone = {e["symbol"] for e in out}
    return ([e for e in entries if e["symbol"] not in gone],
            [{"symbol": e["symbol"], "name": e.get("name", ""), "what": "removed", "reason": "room"} for e in out])


def refresh(state: dict[str, Any], picks: list[dict], view: ScanView, bars_of: Callable, cfg) -> dict[str, Any]:
    """The new state after the session: review, then the picks in, then the cap. `events`
    lists what changed (added / broke_out / removed)."""
    kept, events = review(list(state.get("entries") or []), view, bars_of, max_gap_pct=cfg.max_gap_pct,
                          setup_sessions=cfg.setup_sessions, breakout_sessions=cfg.breakout_sessions)
    added = new_entries(picks, view, bars_of, {e["symbol"] for e in kept})
    events += [{"symbol": e["symbol"], "name": e.get("name", ""), "what": "added", "reason": e["kind"]}
               for e in added]
    entries, gone = cap(kept + added, cfg.max_size)
    events += gone
    managed = sorted(set(state.get("managed") or []) | {e["symbol"] for e in added})
    return {"id": cfg.id, "day": view.day.isoformat(), "entries": entries, "managed": managed,
            "events": events, "synced": False}


# ------------------------------------------------------------------ TradingView
def symbols_of(payload: dict) -> list[str]:
    wl = pick(payload, ["watchlist"], context=GET)
    raw = pick(wl, ["symbols"], context=GET) or []
    return [s for s in raw if isinstance(s, str) and ":" in s and not s.startswith("###")]


async def sync(session, list_id: str, wanted: list[str], managed: list[str], delays) -> dict[str, int]:
    """Make the TradingView list hold `wanted`: add what is missing, remove what this bot
    added and no longer wants. A symbol the owner added by hand stays."""
    current = symbols_of(await fetch_in_session(session, GET, {"watchlist_id": list_id}, delays=delays))
    add = [s for s in wanted if s not in current]
    remove = [s for s in current if s in set(managed) and s not in set(wanted)]
    if add:
        await fetch_in_session(session, ADD, {"watchlist_id": list_id, "symbols": add}, delays=delays)
    if remove:
        await fetch_in_session(session, REMOVE, {"watchlist_id": list_id, "symbols": remove}, delays=delays)
    return {"added": len(add), "removed": len(remove)}


# ------------------------------------------------------------------ the message
def _ticker(symbol: str) -> str:
    return html.escape(symbol.split(":", 1)[-1])


def message(state: dict[str, Any]) -> str | None:
    """The private chat's note on what changed; None when nothing did."""
    events = state.get("events") or []
    if not events:
        return None
    by = {e["symbol"]: e for e in state.get("entries") or []}
    lines = ["📋 <b>רשימת הסטאפים והפריצות בטריידינגוויו עודכנה</b>"]
    added = [e for e in events if e["what"] == "added"]
    broke = [e for e in events if e["what"] == "broke_out"]
    removed = [e for e in events if e["what"] == "removed"]
    if added:
        lines += ["", "<b>נכנסו:</b>"]
        for e in added:
            kind = "אחרי פריצה" if e["reason"] == "breakout" else "סטאפ לפני פריצה"
            lines.append(f"• {_ticker(e['symbol'])}: {html.escape(e['name'])} ({kind})")
    if broke:
        lines += ["", "<b>פרצו (נשארות ברשימה):</b>"]
        lines += [f"• {_ticker(e['symbol'])}: {html.escape(e['name'])}" for e in broke]
    if removed:
        lines += ["", "<b>יצאו:</b>"]
        lines += [f"• {_ticker(e['symbol'])}: {REASONS.get(e['reason'], e['reason'])}" for e in removed]
    setups = sum(e["kind"] == "setup" for e in by.values())
    lines += ["", f"ברשימה עכשיו: {len(by)} מניות ({setups} סטאפים, {len(by) - setups} אחרי פריצה)."]
    return "\n".join(lines)
