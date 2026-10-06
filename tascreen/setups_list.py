"""The bot's own TradingView watchlist: the owner's setups only (owner, 2026-10-05: "focus on
the setups I gave you, accurate and good, as I defined them, without wedges"; the list
itself: 2026-10-02, "refreshes itself and does not keep what did not work out").

The setups, from the chart analyst's own facts (analyst/support.py), each in an uptrend (the
close above a rising 150-day average) and on stocks the bot follows (the universe's market-cap
and volume floors):
- ma150: a test of the 150-day average that held in the last `entry_sessions` sessions, the
  bounce on at least `min_bounce_volume` times the average volume; also a breakout above
  the 150-day average whose retest held so;
- ma20: the same on the 20-day average when it is strong support (holds in a row, the 20-day
  rising);
- retest: a chart-pattern breakout (not a wedge, `exclude_patterns`) or a resistance-zone
  breakout whose retest held so;
- breakout: a chart-pattern breakout (not a wedge) in the last `entry_sessions` sessions, on
  at least `min_breakout_volume` times the average volume: on the list to follow its retest.

At least `min_size` entries (owner, 2026-10-06: "at least ten stocks"): when the strict
setups above are fewer, the list is filled with the same setups at the owner's own words,
the bounce on volume that is not weak (`fill_bounce_volume`) up to `fill_sessions` sessions
back (a fresh breakout on at least average volume), best first; they are marked `filled`.

Once per session, after the evening report:
1. Review (our rules, not Bulkowski's): an entry leaves when a session closes more than
   `support_break_atr` (analyst rules) below its level (the average, or the broken line
   followed with its slope), when a pattern's target is reached (it worked), after
   `keep_sessions` sessions without a new hold, or when the scan no longer covers the stock.
2. In: tonight's setups; a stock already on the list is renewed (a breakout whose retest
   held becomes a retest). The oldest leave first over `max_size`.
3. TradingView: missing symbols are added, and symbols this bot added that left are removed;
   a symbol the owner put there by hand stays. Only this list's id can be edited
   (tv.mcp_client.OwnListSession).
4. What changed goes to the owner's PRIVATE chat.

And `open_after_minutes` after the open (owner, 2026-10-06: "update at the open and the
close"; the live watch runs it, without bars): an entry whose price is already more than
`support_break_atr` ATRs under the level it was left with last night (`watch`, `atr`)
leaves, and the list is refilled from last night's next-best candidates (`pool`) still
above their level, up to `min_size`.

The entries are kept in the private state repo (data/setups_list.json); public logs get
counts only.
"""
from __future__ import annotations

import html
import json
import math
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .fields import pick
from .indicators import atr as atr_series
from .indicators import sma
from .patterns.levels import day_of
from .store import _write_json
from .tv.data import fetch_in_session
from .web.data import ScanView

NAME = "הבוט: סטאפים ופריצות"        # the list's name in TradingView, given when it was created
GET = "mcp-watchlist-get-watchlist"   # {"watchlist": {"id", "name", "symbols": [...]}} (live, 2026-10-02)
ADD = "mcp-watchlist-add-to-watchlist"
REMOVE = "mcp-watchlist-remove-from-watchlist"
VERSION = 2                           # 2026-10-05: the owner's setups (1: the research team's picks)
KINDS = ("retest", "ma150", "breakout", "ma20")   # one entry a stock: the first of these it shows

KIND_HE = {"ma150": "ממוצע 150", "ma20": "ממוצע 20", "retest": "בדיקה אחרי פריצה", "breakout": "פריצה"}
REASONS = {
    "fell_ma": "נסגרה מתחת לממוצע: התמיכה נשברה",
    "fell_back": "נסגרה מתחת לקו שנפרץ: הפריצה לא החזיקה",
    "target": "הגיעה ליעד ✅",
    "stale": "הסטאפ כבר לא טרי: לא התחדש בימים האחרונים",
    "room": "פינוי מקום לחדשות",
    "unfollowed": "כבר לא ברשימת המניות של הבוט (מחזור מסחר או שווי)",
    "redefined": "לא עומדת בהגדרות החדשות של הרשימה",
    "opened_below": "נפתחה מתחת לרמה שלה: התמיכה נשברה בפתיחה",
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


# ------------------------------------------------------------------ the setups of one stock
def _finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _dm(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}"


def setups_of(bars: pd.DataFrame, analysis, cfg) -> list[dict[str, Any]]:
    """The owner's setups in one stock's analysis, best first (KINDS order). Each: kind, text,
    volume (the bounce's or the breakout's, x the 50-day average), since (the session that
    made it), the level to watch and, for a pattern, its key and target."""
    from .analyst.facts import _broken_line

    facts, drawings = analysis.facts, analysis.drawings
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    vs150 = value("vs_sma150_pct")
    if not (_finite(vs150) and float(vs150) > 0 and value("sma150.direction") == "עולה"):
        return []                                          # only in an uptrend
    days = bars["timestamp"].map(day_of).astype(str).tolist()
    last = len(days) - 1
    atr = float(value("atr")) if _finite(value("atr")) else math.nan
    out: list[dict[str, Any]] = []

    def strict(ago: int, vol: float, floor: float) -> bool:
        return ago <= cfg.entry_sessions and vol >= floor

    def held(key: str) -> tuple[int, float] | None:
        """(sessions ago, the bounce's volume) of a hold decided lately on volume that is not
        weak (the filling threshold; `strict` tells the list's own)."""
        text, ago, vol = str(value(key) or ""), value(f"{key}_sessions_ago"), value(f"{key}_volume_ratio")
        if ("החזיק" in text and "נשבר" not in text and isinstance(ago, int) and ago <= cfg.fill_sessions
                and _finite(vol) and float(vol) > cfg.fill_bounce_volume):
            return ago, float(vol)
        return None

    def line_of(key: str) -> dict[str, Any]:
        """A pattern's broken line, as tonight's value and its slope a session."""
        d = drawings[key]
        index = {day: i for i, day in enumerate(days)}
        b = index.get(str(d.get("breakout_day")))
        at = _broken_line(d.get("lines") or [], float(d["breakout"]), index, b if b is not None else last)
        slope = float(at(last) - at(last - 1))
        return {"level": "line", "line": float(at(last)), "slope": slope, "line_day": days[last],
                "watch": float(at(last)) + slope}

    patterns = [k for k, d in drawings.items() if d.get("type") == "pattern" and d.get("family") == "chart"
                and d.get("direction") == "bullish" and d.get("status") == "breakout"
                and d.get("pattern") not in cfg.exclude_patterns and _finite(d.get("breakout"))]
    for key in patterns:                                   # a pattern's breakout retested
        if (h := held(f"{key}.retest")) is not None:
            out.append({"kind": "retest", "pattern": drawings[key]["pattern"],
                        "text": f"{value(f'{key}.retest')} (תבנית {value(f'{key}.name')})",
                        "volume": h[1], "since": days[last - h[0]], "target": value(f"{key}.target"),
                        "strict": strict(*h, cfg.min_bounce_volume), **line_of(key)})
    for key in sorted(k.rsplit(".", 1)[0] for k in facts if k.startswith("zone_") and k.endswith(".retest")):
        if (h := held(f"{key}.retest")) is not None:       # a resistance zone's breakout retested
            out.append({"kind": "retest", "pattern": None, "text": str(value(f"{key}.retest")),
                        "volume": h[1], "since": days[last - h[0]], "target": None,
                        "strict": strict(*h, cfg.min_bounce_volume),
                        "level": "line", "line": float(value(f"{key}.high")), "slope": 0.0,
                        "line_day": days[last], "watch": float(value(f"{key}.high"))})
    if (h := held("sma150.cross.retest")) is not None:     # a breakout above the 150-day retested
        out.append({"kind": "ma150", "text": str(value("sma150.cross.retest")), "volume": h[1],
                    "since": days[last - h[0]], "strict": strict(*h, cfg.min_bounce_volume),
                    "level": "ma", "n": 150, "watch": value("sma150")})
    for n in (150, 20):                                    # a test of an average that held
        k = f"sma{n}.support"
        ago, vol = value(f"{k}.sessions_ago"), value(f"{k}.last_volume_ratio")
        strong = str(value(f"{k}.strength") or "").startswith("תמיכה חזקה")
        if (value(f"{k}.last_result") == "החזיק" and isinstance(ago, int) and ago <= cfg.fill_sessions
                and _finite(vol) and float(vol) > cfg.fill_bounce_volume
                and (n == 150 or (strong and value(f"sma{n}.direction") == "עולה"))):
            out.append({"kind": f"ma{n}", "text": str(value(f"{k}.state")) + (" (תמיכה חזקה)" if strong else ""),
                        "volume": float(vol), "since": days[last - ago],
                        "strict": strict(ago, float(vol), cfg.min_bounce_volume),
                        "level": "ma", "n": n, "watch": value(f"sma{n}")})
    for key in patterns:                                   # a fresh breakout on high volume
        since, vol = value(f"{key}.sessions_since_breakout"), value(f"{key}.breakout_volume_ratio")
        if (isinstance(since, int) and since <= cfg.fill_sessions and _finite(vol) and float(vol) >= 1.0
                and str(value(f"{key}.state")) == "המחיר מעל קו הפריצה"):
            volume = value(f"{key}.breakout_volume")
            out.append({"kind": "breakout", "pattern": drawings[key]["pattern"],
                        "text": (f"פריצה מתבנית {value(f'{key}.name')} ב-{_dm(days[last - since])}"
                                 + (f" בנפח {volume}" if volume else "")),
                        "volume": float(vol), "since": days[last - since], "target": value(f"{key}.target"),
                        "strict": strict(since, float(vol), cfg.min_breakout_volume), **line_of(key)})
    for s in out:
        s["atr"] = atr
    out.sort(key=rank)
    return out


def rank(s: dict[str, Any]) -> tuple:
    """Best first: the list's own thresholds, then the kind (KINDS), the newest, the busiest."""
    return (not s.get("strict", True), KINDS.index(s["kind"]), -date.fromisoformat(s["since"]).toordinal(),
            -(s.get("volume") or 0.0))


def _one(job: tuple[str, str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
    """One stock in a worker process: (symbol, its best setup or None, an error class name)."""
    from .analyst.facts import analyse
    from .store import Store

    root, symbol, cfg = job
    try:
        bars = Store(Path(root)).read_bars(symbol)
        if bars is None or len(bars) < 160:
            return symbol, None, None
        found = setups_of(bars, analyse(bars, symbol), cfg)
        return symbol, (found[0] if found else None), None
    except Exception as exc:                               # one stock never stops the rest
        return symbol, None, type(exc).__name__


def find_setups(root: Path, symbols: list[str], cfg, workers: int | None = None) -> tuple[dict[str, dict], dict[str, int]]:
    """{symbol: its best setup} over `symbols` (the analyst on each, `workers` processes), and
    counts for the public log (errors by class name)."""
    jobs = [(str(root), s, cfg) for s in symbols]
    workers = max(1, min(int(workers or cfg.workers), os.cpu_count() or 1))
    if workers == 1:
        results = map(_one, jobs)
    else:
        pool = ProcessPoolExecutor(max_workers=workers)
        results = pool.map(_one, jobs, chunksize=8)
    found, errors = {}, {}
    try:
        for symbol, setup, error in results:
            if error:
                errors[error] = errors.get(error, 0) + 1
            elif setup is not None:
                found[symbol] = setup
    finally:
        if workers > 1:
            pool.shutdown()
    return found, {"analysed": len(jobs), "setups": len(found), **{f"error {k}": v for k, v in errors.items()}}


# ------------------------------------------------------------------ the review
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


def _after(bars: pd.DataFrame, day: str) -> pd.DataFrame:
    """The bars of the sessions after `day`, up to the newest."""
    if bars is None or bars.empty or not day:
        return pd.DataFrame(columns=["high"])
    return bars.loc[bars["timestamp"].map(day_of) > date.fromisoformat(day[:10])]


def level_now(entry: dict[str, Any], bars: pd.DataFrame) -> float:
    """The level an entry stands on, tonight: its average, or its line moved by its slope."""
    if entry.get("level") == "ma":
        return float(sma(bars["close"], int(entry["n"])).iloc[-1])
    return float(entry["line"]) + float(entry.get("slope") or 0.0) * len(_after(bars, entry["line_day"]))


def review(entries: list[dict], view: ScanView, bars_of: Callable, *, break_atr: float,
           keep_sessions: int) -> tuple[list[dict], list[dict]]:
    """(entries kept, events): each event is {"symbol", "kind", "what": "removed", "reason"}.
    An entry without the session's close is kept as it is (a gap in the data)."""
    kept, events = [], []
    followed = set(view.stocks["symbol"])
    for e in entries:
        def drop(reason: str) -> None:
            events.append({"symbol": e["symbol"], "kind": e.get("kind"), "what": "removed", "reason": reason})

        if e["symbol"] not in followed:                    # left the universe (e.g. the volume floor)
            drop("unfollowed")
            continue
        close, bars = _close(view, e["symbol"]), bars_of(e["symbol"])
        if close is None or bars is None or bars.empty:
            kept.append(e)
            continue
        atr = float(atr_series(bars, 14).iloc[-1])
        level = level_now(e, bars)
        after = _after(bars, e["since"])
        if _finite(atr) and _finite(level) and close < level - break_atr * atr:
            drop("fell_ma" if e.get("level") == "ma" else "fell_back")
        elif _finite(e.get("target")) and len(after) and float(after["high"].max()) >= float(e["target"]):
            drop("target")
        elif len(after) > keep_sessions:
            drop("stale")
        else:                                  # the level the open is checked against tomorrow
            watch = level + (float(e.get("slope") or 0.0) if e.get("level") == "line" else 0.0)
            kept.append({**e, "watch": watch, "atr": atr})
    return kept, events


def cap(entries: list[dict], max_size: int) -> tuple[list[dict], list[dict]]:
    """The newest `max_size` entries; the oldest leave first (then the weakest volume)."""
    if len(entries) <= max_size:
        return entries, []
    ordered = sorted(entries, key=lambda e: (e["since"], e.get("volume") or 0.0, e["symbol"]))
    out = ordered[: len(entries) - max_size]
    gone = {e["symbol"] for e in out}
    return ([e for e in entries if e["symbol"] not in gone],
            [{"symbol": e["symbol"], "kind": e.get("kind"), "what": "removed", "reason": "room"} for e in out])


def refresh(state: dict[str, Any], found: dict[str, dict], view: ScanView, bars_of: Callable, cfg, *,
            break_atr: float) -> dict[str, Any]:
    """The new state after the session: review, tonight's setups in (or renewing an entry),
    then the cap. `events` lists what changed (added / renewed / removed). A list from before
    the owner's setups (version 1) is replaced: its entries leave unless they show one now."""
    events: list[dict] = []
    entries = list(state.get("entries") or [])
    if state.get("version") != VERSION:
        events += [{"symbol": e["symbol"], "kind": e.get("kind"), "what": "removed", "reason": "redefined"}
                   for e in entries if e["symbol"] not in found]
        entries = []
    kept, gone = review(entries, view, bars_of, break_atr=break_atr, keep_sessions=cfg.keep_sessions)
    events += gone
    by = {e["symbol"]: e for e in kept}
    ranked = sorted(found.items(), key=lambda item: (rank(item[1]), item[0]))
    for symbol, setup in ranked:
        old = by.get(symbol)
        if not setup.get("strict", True) and old is None:
            continue                               # a filling candidate: only if the list is short
        entry = {"symbol": symbol, **setup, "added": (old or {}).get("added", view.day.isoformat())}
        if old is None:
            events.append({"symbol": symbol, "kind": setup["kind"], "what": "added", "text": setup["text"]})
        elif (old.get("kind"), old.get("since")) != (setup["kind"], setup["since"]):
            events.append({"symbol": symbol, "kind": setup["kind"], "what": "renewed", "text": setup["text"]})
        by[symbol] = entry
    for symbol, setup in ranked:                   # at least min_size (owner, 2026-10-06)
        if len(by) >= cfg.min_size:
            break
        if symbol not in by:
            by[symbol] = {"symbol": symbol, **setup, "added": view.day.isoformat(), "filled": True}
            events.append({"symbol": symbol, "kind": setup["kind"], "what": "filled", "text": setup["text"]})
    entries, room = cap(list(by.values()), cfg.max_size)
    events += room
    added = {e["symbol"] for e in entries}
    pool = [{"symbol": s, **c} for s, c in ranked if s not in added][: cfg.pool_size]
    managed = sorted(set(state.get("managed") or []) | added)
    return {"version": VERSION, "id": cfg.id, "day": view.day.isoformat(), "entries": entries,
            "pool": pool, "managed": managed, "events": events, "synced": False}


def open_review(state: dict[str, Any], prices: dict[str, float], cfg, *, break_atr: float,
                day: str) -> dict[str, Any]:
    """At the open: an entry already through last night's level by more than `break_atr`
    ATRs leaves; then the pool's best still above their level fill the list to min_size.
    Prices missing for a stock leave it as it is."""
    events: list[dict] = []
    entries: list[dict] = []
    for e in state.get("entries") or []:
        price, watch, atr = prices.get(e["symbol"]), e.get("watch"), e.get("atr")
        if _finite(price) and _finite(watch) and _finite(atr) and float(price) < float(watch) - break_atr * float(atr):
            events.append({"symbol": e["symbol"], "kind": e.get("kind"), "what": "removed", "reason": "opened_below"})
        else:
            entries.append(e)
    have = {e["symbol"] for e in entries}
    pool = []
    for c in state.get("pool") or []:
        price = prices.get(c["symbol"])
        if (len(entries) < cfg.min_size and c["symbol"] not in have and _finite(price)
                and _finite(c.get("watch")) and float(price) >= float(c["watch"])):
            entries.append({**c, "added": day, "filled": True})
            have.add(c["symbol"])
            events.append({"symbol": c["symbol"], "kind": c["kind"], "what": "filled", "text": c["text"]})
        elif c["symbol"] not in have:
            pool.append(c)
    return {**state, "entries": entries, "pool": pool, "events": events, "open_day": day, "synced": False,
            "managed": sorted(set(state.get("managed") or []) | have)}


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


def message(state: dict[str, Any], *, at_open: bool = False) -> str | None:
    """The private chat's note on what changed; None when nothing did."""
    events = state.get("events") or []
    if not events:
        return None
    lines = ["📋 <b>רשימת הסטאפים בטריידינגוויו עודכנה" + (" בפתיחת המסחר" if at_open else "") + "</b>"]
    for what, head in (("added", "נכנסו:"), ("renewed", "התחדשו:"),
                       ("filled", "נכנסו כדי שיהיו לפחות 10 (מחזור לא חלש, או קפיצה מהימים האחרונים):")):
        part = [e for e in events if e["what"] == what]
        if part:
            lines += ["", f"<b>{head}</b>"]
            lines += [f"• {_ticker(e['symbol'])} ({KIND_HE.get(e['kind'], '')}): {html.escape(e['text'])}" for e in part]
    removed = [e for e in events if e["what"] == "removed"]
    if removed:
        lines += ["", "<b>יצאו:</b>"]
        lines += [f"• {_ticker(e['symbol'])}: {REASONS.get(e['reason'], e['reason'])}" for e in removed]
    entries = state.get("entries") or []
    counts = {k: sum(e.get("kind") == k for e in entries) for k in KINDS}
    parts = [f"{counts[k]} {KIND_HE[k]}" for k in KINDS if counts[k]]
    lines += ["", f"ברשימה עכשיו: {len(entries)} מניות" + (f" ({', '.join(parts)})." if parts else ".")]
    return "\n".join(lines)
