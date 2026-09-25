"""Telegram alerts on bullish chart-pattern breakouts (owner's request, 2026-09-24).

- After the close (`run.py --ci-tick`, once per session): the day's confirmed bullish
  breakouts, strongest first (the breakout day's relative volume, then how often the
  pattern reached its target in our own outcome ledger), and the patterns on the verge
  of one; the strongest few get a full analysis (analyst.yml, started through GitHub's
  API; they count toward the analyses' daily limit).
- During the session (`run.py --ci-live`): a live price crossing a forming pattern's
  breakout line; not final until the close.

Every level is the scan's. A target is the book's measure rule, never a forecast. What
was sent is recorded per session (data/alerts/<session>.json), so nothing is sent twice.
Bullish chart patterns only: no candle signals.
"""
from __future__ import annotations

import asyncio
import html
import json
import math
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from .config import AlertsSettings
from .errors import ProviderError
from .fields import pick
from .outcomes import FINAL
from .patterns.levels import invalidation
from .quotes import crossings
from .store import Store, _write_json, symbol_file_stem
from .tv.data import OHLCV_TOOL, fetch_in_session
from .web.data import ScanView, detection_record

MESSAGE_LIMIT = 4000                   # Telegram allows 4096; room for the escaping
VERGE_SHOWN = 30
DISCLAIMER = ("לא ייעוץ השקעות. יעד = כלל המדידה של התבנית, לא תחזית. "
              "ביטול = סגירה מתחת לרמה הזו מבטלת את התבנית.")


# ------------------------------------------------------------------ what was sent
def sent_path(store: Store, session: date) -> Path:
    return store.root / "alerts" / f"{session.isoformat()}.json"


def read_sent(store: Store, session: date) -> dict[str, Any]:
    path = sent_path(store, session)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def write_sent(store: Store, session: date, data: dict[str, Any]) -> None:
    _write_json(sent_path(store, session), data)


# ------------------------------------------------------------------ the day's lists
def hit_rates(ledger: pd.DataFrame | None, min_cases: int) -> dict[str, float]:
    """Pattern -> % of its decided breakouts that reached the target (all sources), only
    for patterns with at least `min_cases` decided."""
    if ledger is None or ledger.empty:
        return {}
    out = {}
    for pattern, group in ledger.groupby("pattern"):
        decided = int(group["outcome"].isin(FINAL).sum())
        if decided >= min_cases and group["target"].notna().any():   # no target, no rate
            out[str(pattern)] = round(float((group["outcome"] == "target").sum()) / decided * 100, 1)
    return out


def _stocks(view: ScanView) -> dict[str, dict[str, Any]]:
    cols = [c for c in ("symbol", "close", "rel_volume", "last_date") if c in view.stocks.columns]
    return {row["symbol"]: row for row in view.stocks[cols].to_dict("records")}


def _finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def bullish_breakouts(view: ScanView, bars_of: Callable[[str], pd.DataFrame | None],
                      rates: dict[str, float]) -> list[dict[str, Any]]:
    """The session's confirmed bullish chart-pattern breakouts, strongest first."""
    d = view.detections
    rows = d.loc[(d["family"] == "chart") & (d["status"] == "breakout")
                 & (d["direction"] == "bullish") & (d["event_day"] == view.day)]
    stocks = _stocks(view)
    out = []
    for row in rows.to_dict("records"):
        stock = stocks.get(row["symbol"], {})
        out.append({"symbol": row["symbol"], "pattern": row["pattern"], "name": row["name_he"],
                    "breakout": row["breakout_price"], "target": row["target"],
                    "invalidation": invalidation(detection_record(row), bars_of(row["symbol"])),
                    "close": stock.get("close", math.nan),
                    "rel_volume": stock.get("rel_volume", math.nan),
                    "hit_rate": rates.get(row["pattern"])})
    out.sort(key=lambda b: (-(b["rel_volume"] if _finite(b["rel_volume"]) else -1.0),
                            -(b["hit_rate"] if b["hit_rate"] is not None else -1.0), b["symbol"]))
    return out


def forming_bullish(view: ScanView) -> list[dict[str, Any]]:
    """Forming chart patterns that break out upward (bullish, or either way) whose stock's
    last bar is the scan's session, with the close's distance below the line."""
    d = view.detections
    rows = d.loc[(d["family"] == "chart") & (d["status"] == "forming")
                 & d["direction"].isin(["bullish", "either"])]
    stocks, day = _stocks(view), view.day.isoformat()
    out = []
    for row in rows.to_dict("records"):
        stock = stocks.get(row["symbol"], {})
        close, line = stock.get("close"), row.get("trigger_up")
        if str(stock.get("last_date", ""))[:10] != day or not (_finite(close) and _finite(line)):
            continue
        gap = (float(line) / float(close) - 1) * 100
        if gap > 0:
            out.append({"symbol": row["symbol"], "pattern": row["pattern"], "name": row["name_he"],
                        "line": float(line), "close": float(close), "gap_pct": gap,
                        "target": row.get("target")})
    out.sort(key=lambda v: (v["gap_pct"], v["symbol"]))
    return out


def on_the_verge(view: ScanView, pct: float) -> list[dict[str, Any]]:
    return [v for v in forming_bullish(view) if v["gap_pct"] <= pct]


# ------------------------------------------------------------------ messages
def _price(x: Any) -> str:
    return f"{float(x):,.2f}" if _finite(x) else "-"


def _day(day: date) -> str:
    return f"{day:%d/%m/%Y}"


def _link(symbol: str, site_url: str) -> str:
    ticker = html.escape(symbol.split(":")[-1])
    return f'<a href="{html.escape(site_url)}/symbol/{symbol_file_stem(symbol)}/">{ticker}</a>'


def _pack(blocks: list[str]) -> list[str]:
    """Blocks joined into messages under Telegram's size limit (a block is never split)."""
    messages, current = [], ""
    for block in blocks:
        joined = f"{current}\n\n{block}" if current else block
        if len(joined) > MESSAGE_LIMIT and current:
            messages.append(current)
            joined = block
        current = joined
    return messages + ([current] if current else [])


def evening_messages(day: date, breakouts: list[dict], verge: list[dict], *, site_url: str,
                     verge_pct: float, coverage: tuple[int, int] | None = None,
                     analyses: list[str] | None = None) -> list[str]:
    head = f"<b>🚀 פריצות שוריות · {_day(day)}</b>\nאחרי הסגירה, מהחזקה לחלשה (לפי הנפח ביום הפריצה)."
    if coverage and coverage[0] < coverage[1]:
        head += f"\n(נסרקו {coverage[0]:,} מתוך {coverage[1]:,} מניות)"
    blocks = [head]
    if not breakouts:
        blocks.append("אין היום פריצות שוריות מאושרות של תבניות גרף.")
    for n, b in enumerate(breakouts, 1):
        volume = f" · נפח x{float(b['rel_volume']):.1f}" if _finite(b["rel_volume"]) else ""
        levels = []
        if _finite(b["target"]):
            levels.append(f"יעד {_price(b['target'])} (כלל המדידה)")
        if _finite(b["invalidation"]):
            levels.append(f"ביטול {_price(b['invalidation'])}")
        if b["hit_rate"] is not None and _finite(b["target"]):
            levels.append(f"בעבר {b['hit_rate']:g}% מהפריצות שלה הגיעו ליעד")
        blocks.append(f"{n}. {_link(b['symbol'], site_url)} · {html.escape(str(b['name']))}\n"
                      f"פריצה {_price(b['breakout'])} · סגירה {_price(b['close'])}{volume}"
                      + (f"\n{' · '.join(levels)}" if levels else ""))
    if analyses:
        blocks.append("📊 ניתוח מלא יגיע בהודעות נפרדות: "
                      + ", ".join(html.escape(s.split(":")[-1]) for s in analyses))
    lines = [f"<b>⏳ על סף פריצה</b> (הסגירה עד {verge_pct:g}% מתחת לקו הפריצה)"]
    if not verge:
        lines.append("אין היום.")
    for n, v in enumerate(verge[:VERGE_SHOWN], 1):
        lines.append(f"{n}. {_link(v['symbol'], site_url)} · {html.escape(str(v['name']))} · "
                     f"קו {_price(v['line'])} · סגירה {_price(v['close'])} ({v['gap_pct']:.1f}% מתחת)")
    if len(verge) > VERGE_SHOWN:
        lines.append(f"ועוד {len(verge) - VERGE_SHOWN} באתר.")
    blocks.append("\n".join(lines))
    blocks.append(f"<i>{html.escape(DISCLAIMER)}</i>")
    return _pack(blocks)


# ------------------------------------------------------------------ the evening report
def evening_report(store: Store, view: ScanView, cfg: AlertsSettings, *, bot: Any, min_cases: int,
                   dispatch: Callable[[str, dict[str, str]], int],
                   can_dispatch: bool, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
                   ) -> dict[str, Any]:
    """Send the session's report once; start the full analyses of the strongest few.
    Returns counts for the public log (no symbols, no prices)."""
    sent = read_sent(store, view.day)
    if sent.get("evening"):
        return {"status": "already sent"}
    breakouts = bullish_breakouts(view, store.read_bars, hit_rates(store.read_ledger(), min_cases))
    verge = on_the_verge(view, cfg.verge_pct)
    chosen = [b["symbol"] for b in breakouts[:cfg.top_analyses]] if can_dispatch else []
    summary = view.summary or {}
    coverage = ((int(summary["symbols_scanned"]), int(summary["symbols_in_universe"]))
                if summary.get("symbols_scanned") and summary.get("symbols_in_universe") else None)
    messages = evening_messages(view.day, breakouts, verge, site_url=cfg.site_url,
                                verge_pct=cfg.verge_pct, coverage=coverage, analyses=chosen)
    for message in messages:
        bot.send(message, html=True)
    started = [s for s in chosen if dispatch("analyst.yml", {"symbol": s}) == 204]
    if len(started) < len(chosen):
        missed = [s.split(":")[-1] for s in chosen if s not in started]
        bot.send(f"לא הצלחתי להפעיל ניתוח מלא עבור: {', '.join(missed)}")
    write_sent(store, view.day, {**sent, "evening": {
        "sent_at": now().isoformat(timespec="seconds"), "messages": len(messages),
        "breakouts": [b["symbol"] for b in breakouts], "verge": len(verge), "analyses": started}})
    return {"status": "sent", "breakouts": len(breakouts), "verge": len(verge),
            "analyses": len(started), "messages": len(messages)}


# ------------------------------------------------------------------ during the session
def watch_list(view: ScanView, watch_pct: float, max_symbols: int) -> list[str]:
    """The stocks to price during the session: a forming bullish pattern's line at most
    `watch_pct` above the last close, nearest first, at most `max_symbols` stocks."""
    out: list[str] = []
    for item in forming_bullish(view):
        if item["gap_pct"] <= watch_pct and item["symbol"] not in out:
            out.append(item["symbol"])
    return out[:max_symbols]


def live_price(payload: dict[str, Any], session_day: date, market_tz: str) -> float | None:
    """The price in a get-ohlcv answer: the newest bar's close, if that bar is today's
    session (before the open the newest bar is yesterday's: no price)."""
    bars = pick(payload, ["bars"], context=f"{OHLCV_TOOL} live price") or []
    if not bars:
        return None
    last = bars[-1]
    opened = datetime.fromtimestamp(int(pick(last, ["t"], context=OHLCV_TOOL)), timezone.utc)
    if opened.astimezone(ZoneInfo(market_tz)).date() != session_day:
        return None
    price = float(pick(last, ["c"], context=OHLCV_TOOL))
    return price if math.isfinite(price) and price > 0 else None


async def fetch_live_prices(session: Any, symbols: list[str], session_day: date, market_tz: str, *,
                            concurrency: int = 1) -> dict[str, Any]:
    """One pass: each watched stock's price from one get-ohlcv call (2 daily bars)."""
    gate = asyncio.Semaphore(concurrency)
    prices: dict[str, float] = {}
    seconds: list[float] = []
    failed = 0

    async def one(symbol: str) -> None:
        nonlocal failed
        async with gate:
            started = time.monotonic()
            try:
                payload = await fetch_in_session(session, OHLCV_TOOL,
                                                 {"symbol": symbol, "interval": "1D", "count": 2})
                price = live_price(payload, session_day, market_tz)
            except (ProviderError, OSError, TimeoutError, ValueError):
                failed += 1
                return
            seconds.append(time.monotonic() - started)
            if price is not None:
                prices[symbol] = price

    await asyncio.gather(*(one(s) for s in symbols))
    return {"prices": prices, "seconds": seconds, "failed": failed}


def live_crossings(view: ScanView, prices: dict[str, float], session_day: date,
                   already: dict[str, Any]) -> list[dict[str, Any]]:
    """Forming patterns whose breakout line the live price is above, not sent yet today."""
    found = crossings(view.stocks, view.detections, prices, session_day)
    found = found.loc[found["direction"] == "bullish"]
    d = view.detections
    out, seen = [], set(already)
    for row in found.itertuples(index=False):
        key = f"{row.symbol}|{row.pattern}"
        if key in seen:
            continue
        seen.add(key)
        match = d.loc[(d["symbol"] == row.symbol) & (d["pattern"] == row.pattern)
                      & (d["status"] == "forming")]
        first = match.iloc[0] if len(match) else None
        out.append({"key": key, "symbol": row.symbol, "pattern": row.pattern,
                    "name": first["name_he"] if first is not None else row.pattern,
                    "line": float(row.level), "price": float(row.price),
                    "target": float(first["target"]) if first is not None and _finite(first["target"]) else math.nan})
    out.sort(key=lambda c: -(c["price"] / c["line"]))
    return out


def live_message(found: list[dict[str, Any]], at: datetime, market_tz: str, site_url: str) -> str:
    lines = [f"<b>⚡ פריצה תוך כדי מסחר · {at.astimezone(ZoneInfo(market_tz)):%H:%M} שעון ניו יורק</b>",
             "לא סופי עד הסגירה. המחירים מעוכבים בכ-15 דקות.", ""]
    for n, c in enumerate(found, 1):
        above = (c["price"] / c["line"] - 1) * 100
        target = f" · יעד {_price(c['target'])} (כלל המדידה)" if _finite(c["target"]) else ""
        lines.append(f"{n}. {_link(c['symbol'], site_url)} · {html.escape(str(c['name']))} · "
                     f"קו {_price(c['line'])} · מחיר {_price(c['price'])} (+{above:.1f}% מעל){target}")
    lines += ["", f"<i>{html.escape('לא ייעוץ השקעות. פריצה מאושרת רק בסגירה.')}</i>"]
    return "\n".join(lines)[:MESSAGE_LIMIT]
