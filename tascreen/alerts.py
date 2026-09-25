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
from .tv.data import NEWS_TOOL, OHLCV_TOOL, fetch_in_session
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
        record = detection_record(row)
        out.append({"symbol": row["symbol"], "pattern": row["pattern"], "name": row["name_he"],
                    "breakout": row["breakout_price"], "target": row["target"],
                    "invalidation": invalidation(record, bars_of(row["symbol"])),
                    "close": stock.get("close", math.nan),
                    "rel_volume": stock.get("rel_volume", math.nan),
                    "hit_rate": rates.get(row["pattern"]), "record": record})
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


def intraday_followup(view: ScanView, live: dict[str, Any]) -> list[dict[str, Any]]:
    """The crossings sent during the session, against the session's close: held (closed
    above the line) or fell back. Held ones first."""
    stocks = _stocks(view)
    d = view.detections
    names = dict(zip(zip(d["symbol"], d["pattern"]), d["name_he"]))
    out = []
    for key, info in live.items():
        symbol, _, pattern = key.partition("|")
        close, line = stocks.get(symbol, {}).get("close"), (info or {}).get("line")
        if _finite(close) and _finite(line):
            out.append({"symbol": symbol, "name": names.get((symbol, pattern), pattern),
                        "line": float(line), "close": float(close), "held": float(close) > float(line)})
    out.sort(key=lambda f: (not f["held"], f["symbol"]))
    return out


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


def breakout_block(n: int, b: dict[str, Any], site_url: str, headline: dict | None = None,
                   limit: int | None = None) -> str:
    """One confirmed breakout's lines (the report's list and each chart's caption). With
    `limit`, the news and then the levels line go before a character is cut."""
    volume = f" · נפח x{float(b['rel_volume']):.1f}" if _finite(b["rel_volume"]) else ""
    levels = []
    if _finite(b["target"]):
        levels.append(f"יעד {_price(b['target'])} (כלל המדידה)")
    if _finite(b["invalidation"]):
        levels.append(f"ביטול {_price(b['invalidation'])}")
    if b["hit_rate"] is not None and _finite(b["target"]):
        levels.append(f"בעבר {b['hit_rate']:g}% מהפריצות שלה הגיעו ליעד")
    head = (f"{n}. {_link(b['symbol'], site_url)} · {html.escape(str(b['name']))}\n"
            f"פריצה {_price(b['breakout'])} · סגירה {_price(b['close'])}{volume}")
    extras = [f"\n{' · '.join(levels)}" if levels else "", f"\n{news_line(headline)}" if headline else ""]
    while limit is not None and len(head + "".join(extras)) > limit and any(extras):
        extras[max(i for i, e in enumerate(extras) if e)] = ""
    return head + "".join(extras)


def evening_messages(day: date, breakouts: list[dict], verge: list[dict], *, site_url: str,
                     verge_pct: float, coverage: tuple[int, int] | None = None,
                     analyses: list[str] | None = None,
                     intraday: list[dict] | None = None,
                     live_summary: dict[str, Any] | None = None,
                     news: dict[str, dict] | None = None) -> list[str]:
    head = f"<b>🚀 פריצות שוריות · {_day(day)}</b>\nאחרי הסגירה, מהחזקה לחלשה (לפי הנפח ביום הפריצה)."
    if coverage and coverage[0] < coverage[1]:
        head += f"\n(נסרקו {coverage[0]:,} מתוך {coverage[1]:,} מניות)"
    blocks = [head]
    if not breakouts:
        blocks.append("אין היום פריצות שוריות מאושרות של תבניות גרף.")
    for n, b in enumerate(breakouts, 1):
        blocks.append(breakout_block(n, b, site_url, (news or {}).get(b["symbol"])))
    if analyses:
        blocks.append("📊 ניתוח מלא יגיע בהודעות נפרדות: "
                      + ", ".join(html.escape(s.split(":")[-1]) for s in analyses))
    if intraday:
        lines = ["<b>⚡ הפריצות מהמסחר של היום, בסגירה</b>"]
        for f in intraday:
            mark = "✅ החזיקה מעל הקו" if f["held"] else "❌ חזרה מתחת לקו"
            lines.append(f"{mark}: {_link(f['symbol'], site_url)} · {html.escape(str(f['name']))} · "
                         f"קו {_price(f['line'])} · סגירה {_price(f['close'])}")
        blocks.append("\n".join(lines))
    lines = [f"<b>⏳ על סף פריצה</b> (הסגירה עד {verge_pct:g}% מתחת לקו הפריצה)"]
    if not verge:
        lines.append("אין היום.")
    for n, v in enumerate(verge[:VERGE_SHOWN], 1):
        lines.append(f"{n}. {_link(v['symbol'], site_url)} · {html.escape(str(v['name']))} · "
                     f"קו {_price(v['line'])} · סגירה {_price(v['close'])} ({v['gap_pct']:.1f}% מתחת)")
    if len(verge) > VERGE_SHOWN:
        lines.append(f"ועוד {len(verge) - VERGE_SHOWN} באתר.")
    blocks.append("\n".join(lines))
    if live_summary and live_summary.get("passes"):
        delay = live_summary.get("data_delay_min")
        blocks.append(f"🔎 מעקב המסחר היום: {live_summary['passes']} סבבים · "
                      f"{live_summary.get('watched', 0)} מניות במעקב · {live_summary.get('alerts', 0)} התראות"
                      + (f" · עיכוב הנתונים כ-{float(delay):g} דק'" if _finite(delay) else ""))
    blocks.append(f"<i>{html.escape(DISCLAIMER)}</i>")
    return _pack(blocks)


# ------------------------------------------------------------------ the evening report
def evening_report(store: Store, view: ScanView, cfg: AlertsSettings, *, bot: Any, min_cases: int,
                   dispatch: Callable[[str, dict[str, str]], int],
                   can_dispatch: bool, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                   live_summary: dict[str, Any] | None = None,
                   news_of: Callable[[list[str]], dict[str, dict]] | None = None,
                   images: bool = False, to_png: Callable[[str], bytes] | None = None) -> dict[str, Any]:
    """Send the session's report once, then (with `images`) each breakout's pattern chart
    in albums; start the full analyses of the strongest few. Returns counts for the
    public log (no symbols, no prices)."""
    sent = read_sent(store, view.day)
    if sent.get("evening"):
        return {"status": "already sent"}
    breakouts = bullish_breakouts(view, store.read_bars, hit_rates(store.read_ledger(), min_cases))
    verge = on_the_verge(view, cfg.verge_pct)
    chosen = [b["symbol"] for b in breakouts[:cfg.top_analyses]] if can_dispatch else []
    summary = view.summary or {}
    coverage = ((int(summary["symbols_scanned"]), int(summary["symbols_in_universe"]))
                if summary.get("symbols_scanned") and summary.get("symbols_in_universe") else None)
    intraday = intraday_followup(view, sent.get("live") or {})
    news = news_of([b["symbol"] for b in breakouts]) if news_of and breakouts else {}
    messages = evening_messages(view.day, breakouts, verge, site_url=cfg.site_url,
                                verge_pct=cfg.verge_pct, coverage=coverage, analyses=chosen,
                                intraday=intraday, live_summary=live_summary, news=news)
    for message in messages:
        bot.send(message, html=True)
    photos, no_chart = ([], 0)
    if images and breakouts:
        photos, no_chart = breakout_photos(breakouts, store.read_bars, cfg.site_url, news,
                                           to_png or default_png)
        try:
            bot.send_album(photos)
        except (ProviderError, OSError) as exc:          # the report itself went out
            import logging
            logging.getLogger(__name__).warning("breakout charts not sent: %s", type(exc).__name__)
            no_chart, photos = no_chart + len(photos), []
    started = [s for s in chosen if dispatch("analyst.yml", {"symbol": s}) == 204]
    if len(started) < len(chosen):
        missed = [s.split(":")[-1] for s in chosen if s not in started]
        bot.send(f"לא הצלחתי להפעיל ניתוח מלא עבור: {', '.join(missed)}")
    write_sent(store, view.day, {**sent, "evening": {
        "sent_at": now().isoformat(timespec="seconds"), "messages": len(messages),
        "breakouts": [b["symbol"] for b in breakouts], "verge": len(verge), "analyses": started}})
    return {"status": "sent", "breakouts": len(breakouts), "verge": len(verge),
            "analyses": len(started), "messages": len(messages),
            "intraday_held": sum(f["held"] for f in intraday), "intraday_fell": sum(not f["held"] for f in intraday),
            "charts": len(photos), "charts_missing": no_chart}


# ------------------------------------------------------------------ during the session
def watch_list(view: ScanView, watch_pct: float, max_symbols: int) -> list[str]:
    """The stocks to price during the session: a forming bullish pattern's line at most
    `watch_pct` above the last close, nearest first, at most `max_symbols` stocks."""
    out: list[str] = []
    for item in forming_bullish(view):
        if item["gap_pct"] <= watch_pct and item["symbol"] not in out:
            out.append(item["symbol"])
    return out[:max_symbols]


def watch_tiers(view: ScanView, verge_pct: float, watch_pct: float,
                max_symbols: int) -> tuple[list[str], list[str]]:
    """The watch list split in two: stocks whose nearest line is within `verge_pct`
    (priced every pass) and the rest (priced every few passes: they need a bigger move)."""
    nearest: dict[str, float] = {}
    for item in forming_bullish(view):
        nearest.setdefault(item["symbol"], item["gap_pct"])      # sorted: the nearest first
    watched = watch_list(view, watch_pct, max_symbols)
    return ([s for s in watched if nearest[s] <= verge_pct],
            [s for s in watched if nearest[s] > verge_pct])


def bar_age_minutes(payload: dict[str, Any], now: datetime) -> float | None:
    """How old the newest 1-minute bar is: about 1 with real-time data, about 15 or more
    with delayed data. Measures TradingView's delay during the session."""
    bars = pick(payload, ["bars"], context=f"{OHLCV_TOOL} 1-minute bars") or []
    if not bars:
        return None
    opened = datetime.fromtimestamp(int(pick(bars[-1], ["t"], context=OHLCV_TOOL)), timezone.utc)
    return round((now - opened).total_seconds() / 60, 1)


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
                            concurrency: int = 1, measure_delay: bool = False) -> dict[str, Any]:
    """One pass: each watched stock's price from one get-ohlcv call (2 daily bars). With
    `measure_delay`, one more call (the first stock's newest 1-minute bar) shows how
    delayed TradingView's data is."""
    gate = asyncio.Semaphore(concurrency)
    prices: dict[str, float] = {}
    seconds: list[float] = []
    failed = 0
    delay = None
    if measure_delay and symbols:
        try:
            payload = await fetch_in_session(session, OHLCV_TOOL,
                                             {"symbol": symbols[0], "interval": "1", "count": 1})
            delay = bar_age_minutes(payload, datetime.now(timezone.utc))
        except (ProviderError, OSError, TimeoutError, ValueError):
            delay = None

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
    return {"prices": prices, "seconds": seconds, "failed": failed, "delay_min": delay}


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
                    "target": float(first["target"]) if first is not None and _finite(first["target"]) else math.nan,
                    "record": detection_record(first.to_dict()) if first is not None else None})
    out.sort(key=lambda c: -(c["price"] / c["line"]))
    return out


def watch_started_message(near: int, far: int, cfg: AlertsSettings) -> str:
    """Sent once a session when the live watch starts, so a quiet day is not a doubt."""
    return (f"🔎 <b>המעקב במהלך המסחר התחיל</b>\n"
            f"{near + far} מניות במעקב: {near} עד {cfg.verge_pct:g}% מקו הפריצה (כל "
            f"{cfg.live_interval_minutes:g} דקות), {far} רחוקות יותר (כל "
            f"{cfg.live_interval_minutes * cfg.far_every:g} דקות).\n"
            "תגיע הודעה רק כשמניה חוצה את קו הפריצה.")


def live_message(found: list[dict[str, Any]], at: datetime, market_tz: str, site_url: str,
                 news: dict[str, dict] | None = None) -> str:
    head = [f"<b>⚡ פריצה תוך כדי מסחר · {at.astimezone(ZoneInfo(market_tz)):%H:%M} שעון ניו יורק</b>",
            "לא סופי עד הסגירה. מחירי TradingView עשויים להיות מעוכבים.", ""]
    tail = ["", f"<i>{html.escape('לא ייעוץ השקעות. פריצה מאושרת רק בסגירה.')}</i>"]
    items: list[str] = []
    for n, c in enumerate(found, 1):
        above = (c["price"] / c["line"] - 1) * 100
        target = f" · יעד {_price(c['target'])} (כלל המדידה)" if _finite(c["target"]) else ""
        headline = news_line((news or {}).get(c["symbol"]))
        item = (f"{n}. {_link(c['symbol'], site_url)} · {html.escape(str(c['name']))} · "
                f"קו {_price(c['line'])} · מחיר {_price(c['price'])} (+{above:.1f}% מעל){target}"
                + (f"\n{headline}" if headline else ""))
        if len("\n".join(head + items + [item] + tail)) > MESSAGE_LIMIT - 40:      # never cut a tag
            items.append(f"ועוד {len(found) - n + 1} באתר.")
            break
        items.append(item)
    return "\n".join(head + items + tail)


# ------------------------------------------------------------------ news
NEWS_MAX_AGE_HOURS = 48
NEWS_MAX_RELATED = 3         # a headline naming many stocks is about something else


def pick_headline(payload: dict[str, Any], symbol: str, now: datetime) -> dict[str, Any] | None:
    """The newest headline about `symbol` itself (it names at most NEWS_MAX_RELATED stocks)
    from the last NEWS_MAX_AGE_HOURS, from a get-news answer; None when there is none.
    Shape (live, 2026-09-25): {data: {headlines: [{title, published (unix), link,
    provider: {name}, relatedSymbols: [{symbol}], urgency, ...}]}}."""
    data = pick(payload, ["data"], context=NEWS_TOOL)
    best = None
    for row in pick(data, ["headlines"], context=NEWS_TOOL) or []:
        related = [pick(r, ["symbol"], context=NEWS_TOOL) for r in (row.get("relatedSymbols") or [])]
        if symbol not in related or len(related) > NEWS_MAX_RELATED:
            continue
        published = datetime.fromtimestamp(int(pick(row, ["published"], context=NEWS_TOOL)), timezone.utc)
        hours = (now - published).total_seconds() / 3600
        if not 0 <= hours <= NEWS_MAX_AGE_HOURS:
            continue
        if best is None or published > best["published"]:
            provider = row.get("provider") or {}
            best = {"title": str(pick(row, ["title"], context=NEWS_TOOL)).strip(), "published": published,
                    "hours": hours, "provider": str(provider.get("name") or "").strip(),
                    "link": str(row.get("link") or "")}
    return best if best and best["title"] else None


async def fetch_news(session: Any, symbols: list[str], now: datetime) -> dict[str, dict[str, Any]]:
    """The headline for each symbol (English: TradingView had none in Hebrew). A failed
    call means no headline, never a failed report."""
    out: dict[str, dict[str, Any]] = {}
    for symbol in dict.fromkeys(symbols):
        try:
            payload = await fetch_in_session(session, NEWS_TOOL, {"symbol": symbol, "lang": "en", "limit": 10})
            headline = pick_headline(payload, symbol, now)
        except (ProviderError, OSError, TimeoutError, ValueError):
            continue
        if headline:
            out[symbol] = headline
    return out


def news_line(headline: dict[str, Any] | None) -> str:
    """One line under an alert: the headline as published (no interpretation), its
    source and age, linked to TradingView's story page."""
    if not headline:
        return ""
    title = headline["title"] if len(headline["title"]) <= 140 else headline["title"][:137] + "..."
    hours = headline["hours"]
    age = "לפני פחות משעה" if hours < 1 else f"לפני {hours:.0f} שעות"
    source = f"{headline['provider']}, " if headline["provider"] else ""
    text = html.escape(title)
    if headline["link"].startswith("https://www.tradingview.com/"):
        text = f'<a href="{html.escape(headline["link"])}">{text}</a>'
    return f"📰 {text} ({html.escape(source)}{age})"


# ------------------------------------------------------------------ pattern charts
# The owner's request (2026-09-25): every bullish breakout with a chart of its pattern and
# the breakout marked; the channels' renderer draws exactly that.
BREAKOUT_DRAWINGS = ["pattern_lines", "pivots", "confirm_line", "breakout", "target", "failure", "volume"]
CROSSING_DRAWINGS = ["pattern_lines", "pivots", "confirm_line", "trigger", "volume"]
CAPTION_LIMIT = 1000                  # Telegram's caption limit is 1024


def default_png(svg: str) -> bytes:
    import tempfile

    from .analyst.png import svg_to_png

    with tempfile.TemporaryDirectory(prefix="ta-alert-", ignore_cleanup_errors=True) as folder:
        path = Path(folder) / "chart.svg"
        path.write_text(svg, encoding="utf-8")
        return svg_to_png(path, Path(folder) / "chart.png").read_bytes()


def pattern_chart(bars: pd.DataFrame, record: dict[str, Any], *, live_price: float | None = None) -> str:
    from .channels.chart_svg import render

    drawings = CROSSING_DRAWINGS if live_price is not None else BREAKOUT_DRAWINGS
    symbol = str(record.get("symbol", ""))
    return render(bars, record, drawings, "", seed=f"alert-{symbol}", title=symbol, live_price=live_price)


def _photo(bars: pd.DataFrame | None, record: dict | None, caption: str, to_png: Callable[[str], bytes],
           live_price: float | None = None) -> tuple[bytes, str] | None:
    """A chart and its caption; None if it cannot be drawn (the text still goes out)."""
    if bars is None or bars.empty or not record:
        return None
    try:
        return to_png(pattern_chart(bars, record, live_price=live_price)), caption
    except Exception as exc:                        # a picture is never worth a lost alert
        import logging
        logging.getLogger(__name__).warning("chart for %s failed: %s", record.get("symbol"),
                                            type(exc).__name__)
        return None


def breakout_photos(breakouts: list[dict], bars_of: Callable[[str], pd.DataFrame | None], site_url: str,
                    news: dict[str, dict] | None = None,
                    to_png: Callable[[str], bytes] = default_png) -> tuple[list[tuple[bytes, str]], int]:
    """The confirmed breakouts' charts (strongest first) with their report lines as captions."""
    photos, failed = [], 0
    for n, b in enumerate(breakouts, 1):
        caption = breakout_block(n, b, site_url, (news or {}).get(b["symbol"]), limit=CAPTION_LIMIT)
        photo = _photo(bars_of(b["symbol"]), b.get("record"), caption, to_png)
        if photo is None:
            failed += 1
        else:
            photos.append(photo)
    return photos, failed


def crossing_caption(c: dict[str, Any], at: datetime, market_tz: str, site_url: str,
                     headline: dict | None = None) -> str:
    above = (c["price"] / c["line"] - 1) * 100
    target = f" · יעד {_price(c['target'])} (כלל המדידה)" if _finite(c["target"]) else ""
    lines = [f"<b>⚡ פריצה תוך כדי מסחר · {at.astimezone(ZoneInfo(market_tz)):%H:%M} ניו יורק</b>",
             f"{_link(c['symbol'], site_url)} · {html.escape(str(c['name']))} · קו {_price(c['line'])} · "
             f"מחיר {_price(c['price'])} (+{above:.1f}% מעל){target}"]
    news = news_line(headline)
    if news and len("\n".join(lines + [news])) < CAPTION_LIMIT - 80:
        lines.append(news)
    lines.append(f"<i>{html.escape('לא סופי עד הסגירה. לא ייעוץ השקעות.')}</i>")
    return "\n".join(lines)


def crossing_photos(found: list[dict], bars_of: Callable[[str], pd.DataFrame | None], at: datetime,
                    market_tz: str, site_url: str, news: dict[str, dict] | None = None,
                    to_png: Callable[[str], bytes] = default_png) -> list[tuple[bytes, str]]:
    """Each live crossing's forming pattern, its breakout line and the live price."""
    photos = []
    for c in found:
        caption = crossing_caption(c, at, market_tz, site_url, (news or {}).get(c["symbol"]))
        photo = _photo(bars_of(c["symbol"]), c.get("record"), caption, to_png, live_price=c["price"])
        if photo is not None:
            photos.append(photo)
    return photos


async def fetch_bars(session: Any, symbols: list[str], before: date, count: int = 260) -> dict[str, pd.DataFrame]:
    """Daily bars for the live watch's charts (it restores no bars): each stock's last
    `count` sessions before `before` (today's unfinished bar left out)."""
    from .tv.data import bars_frame

    out: dict[str, pd.DataFrame] = {}
    for symbol in dict.fromkeys(symbols):
        try:
            payload = await fetch_in_session(session, OHLCV_TOOL, {"symbol": symbol, "interval": "1D", "count": count})
            bars = bars_frame(payload, symbol)
        except (ProviderError, OSError, TimeoutError, ValueError):
            continue
        bars = bars.loc[bars["timestamp"].dt.date < before].reset_index(drop=True)
        if len(bars):
            out[symbol] = bars
    return out
