"""The website: FastAPI + Jinja2, Hebrew and right-to-left, bound to 127.0.0.1.

The owner may expose it through a tunnel (VS Code port forwarding); the tunnel's
host names are listed in `web.public_hosts`, which also turns on public mode:
no local details (error texts, file paths) are shown.

Pages:  /  (#כללי)   /c/{channel}   /screener   /symbol/{EXCHANGE:TICKER}   /patterns
        /scorecard   /status
API:    /api/scan (same filters as /screener)   /api/symbol/{EXCHANGE:TICKER}   /api/live
        /api/channels   /api/channels/{channel}   /api/scorecard   /api/stamp

The discussion channels (the home page) show threads written by simulated members,
labelled as AI agents (see tascreen/channels). Screener links from before the
channels (/?family=...) are redirected to /screener.

Everything is read from data/ (the newest scan, the stored bars, the newest live
quotes) and logs/; nothing here calls TradingView. During the session, fresh
quotes replace the last close in the price columns, and chart patterns still
forming whose breakout level the live price has passed are shown as crossings,
not final until the close (see tascreen/quotes.py).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from ..channels.select import GENERAL, Channel, channel_name, find
from ..config import Settings
from ..indicators import sma
from ..outcomes import recent_decided, scorecard
from ..patterns.rules import Rules, load_rules
from ..store import Store, symbol_file_stem
from . import fmt, labels
from .channels import ChannelRepository
from .data import Live, QuotesRepository, ScanRepository, ScanView, live_for, with_live_prices
from .filters import SORTS, STATUSES, Query, apply, parse_query

HOST = "127.0.0.1"          # the bind address is never configurable; tunnels connect here
WEB_DIR = Path(__file__).resolve().parent
SYMBOL = re.compile(r"^[A-Z]{1,12}:[A-Z0-9.\-_]{1,20}$")
NOT_ADVICE = ("Mechanical readings of past prices, not investment advice. A target is the "
              "book's measure rule applied to the pattern's height, not a forecast.")

API_COLUMNS = ("symbol", "ticker", "description", "exchange", "sector", "industry", "market_cap",
               "last_date", "close", "change_1d_pct", "change_5d_pct", "change_20d_pct",
               "rsi14", "sma20", "sma50", "sma150", "above_sma50", "above_sma150", "atr_pct",
               "rel_volume", "pct_from_52w_high", "pct_from_52w_low", "avg_dollar_volume_20d",
               "golden_cross_days_ago", "death_cross_days_ago", "next_earnings", "coverage",
               "patterns_skipped")


# Query parameters of the screener: at "/" they mean an old screener link.
SCREENER_KEYS = ({f.name for f in fields(Query)} | {"pattern", "status"}) - {"errors", "patterns", "statuses"}


def symbol_url(symbol: str) -> str:
    return "/symbol/" + quote(symbol, safe=":")


def static_symbol_url(symbol: str) -> str:
    """The exported site's page: a folder per symbol, no ':' in a file name."""
    return f"/symbol/{symbol_file_stem(symbol)}/"


# The screener's quick filters: (slug for the exported site, query, label).
PRESETS = (
    ("recent-chart-breakouts", "family=chart&status=breakout&within=5&sort=age",
     "פריצות מתבניות גרף, 5 ימים אחרונים"),
    ("chart-forming", "family=chart&status=forming", "תבניות גרף בבנייה"),
    ("bullish-candles", "family=candle&direction=bullish&within=1", "נרות שוריים ביום האחרון"),
    ("bearish-candles", "family=candle&direction=bearish&within=1", "נרות דוביים ביום האחרון"),
    ("near-high-volume", "near_high=3&relvol_min=1.5", "ליד שיא שנתי, בנפח גבוה"),
    ("rsi-under-30", "rsi_max=30", "RSI מתחת ל-30"),
    ("golden-cross", "cross=golden", "חציית זהב"),
)
STATIC_ROWS = 500          # rows on an exported screener page (the full filter form returns later)


def screener_link(query: str = "", *, static: bool = False) -> str:
    """A link to the screener with `query`; on the exported site, the page made for it
    (the default view, a pattern, or a preset)."""
    if not static:
        return "/screener" + (f"?{query}" if query else "")
    if not query:
        return "/screener/"
    if query.startswith("pattern=") and "&" not in query:
        return f"/screener/pattern/{query.split('=', 1)[1]}/"
    for slug, preset, _ in PRESETS:
        if query == preset:
            return f"/screener/preset/{slug}/"
    return "/screener/"


def tradingview_url(symbol: str) -> str:
    return "https://www.tradingview.com/chart/?symbol=" + quote(symbol, safe="")


def back_link(request: Request) -> str:
    """The screener page the visitor came from, as a path on this site, or ''.

    Only a referer from this same host counts, so another site can never place
    its own link on the page."""
    parts = urlsplit(request.headers.get("referer", ""))
    if parts.netloc != request.headers.get("host") or not parts.path.startswith("/") \
            or parts.path.startswith(("//", "/symbol/")):
        return ""
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _iso(ts) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).date().isoformat()


def chart_payload(bars: pd.DataFrame, detections: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything the symbol page's chart draws, keyed by 'YYYY-MM-DD' session dates."""
    days = bars["timestamp"].dt.strftime("%Y-%m-%d").tolist()
    o, h, l, c, v = (bars[k].tolist() for k in ("open", "high", "low", "close", "volume"))

    def line(values: pd.Series) -> list[dict[str, Any]]:
        return [{"time": d, "value": round(float(x), 4)} for d, x in zip(days, values)
                if fmt.ok(x)]

    return {
        "candles": [{"time": d, "open": a, "high": b, "low": e, "close": f}
                    for d, a, b, e, f in zip(days, o, h, l, c)],
        "volume": [{"time": d, "value": x if fmt.ok(x) else 0, "up": f >= a}
                   for d, x, a, f in zip(days, v, o, c)],
        "sma50": line(sma(bars["close"], 50)),
        "sma150": line(sma(bars["close"], 150)),
        "detections": [{
            "id": i, "family": d["family"], "pattern": d["pattern"], "name": d["name_he"],
            "direction": d["direction"], "status": d["status"],
            "start": _iso(d["start"]), "end": _iso(d["end"]),
            "breakout_date": _iso(d["breakout_date"]),
            "breakout_price": d["breakout_price"], "target": d["target"],
            "trigger_up": d.get("trigger_up"), "trigger_down": d.get("trigger_down"),
            "points": d["points"], "lines": d["lines"],
        } for i, d in enumerate(detections)],
    }


def _short(x: float) -> float:
    """A price with 5 significant digits (enough for a chart, a third of the text)."""
    return float(f"{float(x):.5g}")


def compact_chart_payload(bars: pd.DataFrame, detections: list[dict[str, Any]],
                          min_bars: int = 200) -> dict[str, Any]:
    """chart_payload for the exported site: column arrays instead of one object per
    bar, and only the last `min_bars` sessions (or from the oldest detection's start),
    with the averages computed on the full history first. symbol.js expands it."""
    first = max(0, len(bars) - min_bars)
    days_all = bars["timestamp"].dt.strftime("%Y-%m-%d")
    starts = [_iso(d["start"]) for d in detections if _iso(d["start"])]
    if starts:
        oldest = int((days_all < min(starts)).sum())
        first = min(first, max(0, oldest - 10))
    window = bars.iloc[first:]
    sma50, sma150 = (sma(bars["close"], n).iloc[first:] for n in (50, 150))

    def column(values) -> list:
        return [_short(x) if fmt.ok(x) else None for x in values]

    full = chart_payload(bars.iloc[first:], detections)
    return {
        "cols": {"t": days_all.iloc[first:].tolist(),
                 "o": column(window["open"]), "h": column(window["high"]),
                 "l": column(window["low"]), "c": column(window["close"]),
                 "v": [int(x) if fmt.ok(x) else 0 for x in window["volume"]],
                 "sma50": column(sma50), "sma150": column(sma150)},
        "detections": full["detections"],
    }


def live_summary(live: Live | None) -> dict[str, Any]:
    if live is None:
        return {"active": False, "stale": False, "session": None, "crossings": 0}
    return {"active": live.active, "stale": live.stale, "session": live.quotes.session,
            "fetched_at": live.quotes.fetched_at,
            "crossings": sum(len(v) for v in live.crossings.values()),
            "note": "Quotes are delayed; a crossing is not a breakout until the session's close."}


def create_app(settings: Settings, *, rules: Rules | None = None, static: bool = False,
               static_stamp: str = "") -> FastAPI:
    """`static`: pages for the exported site (tascreen/web/export.py): links to its files,
    compact charts, no live data, and `static_stamp` as the reload stamp."""
    store = Store(settings.data_dir)
    rules = rules or load_rules()
    repo = ScanRepository(store, rules)
    quotes_repo = QuotesRepository(store)
    channel_repo = ChannelRepository(store, rules, settings.channels)
    specs = {**rules.chart, **rules.candle}
    display_tz = ZoneInfo(settings.web.display_timezone)
    max_age = timedelta(minutes=settings.live.interval_minutes * settings.live.stale_after_intervals)

    def current() -> tuple[ScanView | None, Live | None]:
        view = repo.current()
        if static:                     # the exported site gets live data in the browser
            return view, None
        return view, live_for(view, quotes_repo.current(), datetime.now(timezone.utc), max_age)

    def clock(when) -> str:
        if isinstance(when, str):
            when = datetime.fromisoformat(when)
        return when.astimezone(display_tz).strftime("%H:%M") if when else fmt.MISSING

    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    templates.env.filters.update(
        num=fmt.num, price=fmt.price, pct=fmt.pct, money=fmt.money, day=fmt.day,
        sign=fmt.sign_class, check_text=labels.check_text, sector=labels.sector,
        symbol_url=static_symbol_url if static else symbol_url, tradingview_url=tradingview_url,
        clock=clock, post_text=fmt.post_text)
    public = settings.web.public or static
    templates.env.globals.update(labels=labels, rules=rules, specs=specs, SORTS=SORTS,
                                 STATUSES=STATUSES, ok=fmt.ok, public=public, GENERAL=GENERAL,
                                 static=static, PRESETS=PRESETS, STATIC_ROWS=STATIC_ROWS,
                                 screener_link=lambda q="": screener_link(q, static=static))

    app = FastAPI(title="ta-screener", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return response

    # Only these names: a page on another site cannot reach this server through a
    # rebinding DNS name. public_hosts adds the tunnel's address when the owner
    # exposes the site (the server itself still binds to 127.0.0.1).
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=[HOST, "localhost", *settings.web.public_hosts])
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    def stamp() -> str:
        if static:
            return static_stamp
        quotes = quotes_repo.current()
        return f"{quotes.summary.get('fetched_at') if quotes else ''}|{channel_repo.stamp()}"

    def render(request: Request, name: str, view: ScanView | None, status_code: int = 200,
               live: Live | None = None, **context: Any) -> HTMLResponse:
        stale_rules = bool(view and view.summary.get("rules_digest") != rules.digest) and not static
        sidebar = {"channels": channel_repo.channel_list(view), "fresh": channel_repo.fresh(),
                   "current": context.get("channel").id if context.get("channel") else None}
        return templates.TemplateResponse(
            request, name, {"view": view, "stale_rules": stale_rules, "path": request.url.path,
                            "live": live, "sidebar": sidebar, "stamp": stamp(), **context},
            status_code=status_code)

    def not_found(request: Request, view: ScanView | None, what: str,
                  kind: str = "stock") -> HTMLResponse:
        return render(request, "notfound.html", view, status_code=404, what=what, kind=kind)

    def channel_view(request: Request, channel_id: str) -> HTMLResponse:
        view, live = current()
        if not channel_repo.known(channel_id):
            return not_found(request, view, channel_id, kind="channel")
        spec = specs.get(channel_id)
        channel = find(channel_repo.channel_list(view), channel_id)
        if channel is None:              # a pattern outside the popular list: still readable
            count = int((view.detections["pattern"] == channel_id).sum()) if view else 0
            channel = Channel(channel_id, channel_name(spec.name_he), spec.name_he, count, spec.family)
        return render(request, "channel.html", view, live=live, channel=channel, spec=spec,
                      threads=channel_repo.threads(channel_id), members=len(channel_repo.personas))

    if static:
        @app.get("/__not_found__", response_class=HTMLResponse)
        def not_found_page(request: Request):
            return not_found(request, repo.current(), "", kind="page")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        if SCREENER_KEYS & set(request.query_params.keys()):
            return RedirectResponse(f"/screener?{request.url.query}", status_code=307)
        return channel_view(request, GENERAL)

    @app.get("/c/{channel_id}", response_class=HTMLResponse)
    def channel_page(request: Request, channel_id: str):
        return channel_view(request, channel_id.strip().lower())

    @app.get("/screener", response_class=HTMLResponse)
    def screener(request: Request):
        view, live = current()
        if view is None:
            return render(request, "empty.html", None)
        query = parse_query(request.query_params, rules)
        result = apply(with_live_prices(view, live), query,
                       STATIC_ROWS if static else settings.web.rows_per_page,
                       crossings=live.crossings if live else None)
        stocks = view.stocks
        facets = {
            "sectors": sorted(((labels.sector(s), s) for s in stocks["sector"].dropna().unique()
                               if s), key=lambda pair: pair[0]),
            "exchanges": sorted(stocks["exchange"].dropna().unique()),
            "counts": view.detections["pattern"].value_counts().to_dict(),
        }
        return render(request, "screener.html", view, live=live, query=query, result=result,
                      facets=facets)

    @app.get("/symbol/{symbol}", response_class=HTMLResponse)
    def symbol_page(request: Request, symbol: str):
        view, live = current()
        symbol = symbol.strip().upper()
        stock = (with_live_prices(view, live).stock(symbol)
                 if view is not None and SYMBOL.match(symbol) else None)
        if stock is None:
            return not_found(request, view, symbol)
        detections = view.detections_of(symbol)
        bars = store.read_bars(symbol)
        payload = compact_chart_payload if static else chart_payload
        chart = fmt.script_json(payload(bars, detections)) if bars is not None else None
        crossing = {c["pattern"]: c for c in live.crossings.get(symbol, [])} if live else {}
        return render(request, "symbol.html", view, live=live, stock=stock, detections=detections,
                      chart=chart, back="" if static else back_link(request), crossing=crossing)

    @app.get("/patterns", response_class=HTMLResponse)
    def patterns_page(request: Request):
        view = repo.current()
        counts = view.detections["pattern"].value_counts().to_dict() if view is not None else {}
        return render(request, "patterns.html", view, counts=counts)

    @app.get("/scorecard", response_class=HTMLResponse)
    def scorecard_page(request: Request):
        view, live = current()
        ledger = store.read_ledger()
        rows = scorecard(ledger, settings.outcomes.min_cases)
        meta = store.read_outcomes_meta()
        updated = (datetime.fromisoformat(meta["updated_at"]).astimezone(display_tz)
                   .strftime("%d/%m/%Y %H:%M") if meta.get("updated_at") else None)
        return render(request, "scorecard.html", view, live=live, rows=rows, updated=updated,
                      sources=[s for s in labels.SOURCE if any(r["source"] == s for r in rows)],
                      recent=recent_decided(ledger, 15),
                      window=settings.outcomes.max_sessions, min_cases=settings.outcomes.min_cases,
                      tracked=0 if ledger is None else len(ledger))

    @app.get("/api/scorecard")
    def api_scorecard():
        ledger = store.read_ledger()
        return JSONResponse(fmt.clean({
            "note": NOT_ADVICE + " Outcomes use this site's own detector and definitions, not "
                    "the book's statistics.",
            "tracked_sessions": settings.outcomes.max_sessions,
            "min_cases_for_percentages": settings.outcomes.min_cases,
            "updated_at": store.read_outcomes_meta().get("updated_at"),
            "patterns": scorecard(ledger, settings.outcomes.min_cases)}))

    @app.get("/status", response_class=HTMLResponse)
    def status_page(request: Request):
        view, live = current()
        bar_status = store.read_status()
        universe_days = store.universe_days()
        universe = (_read_json(store.universe_dir / f"{universe_days[-1].isoformat()}.json")
                    if universe_days else None)
        return render(
            request, "status.html", view, live=live, quotes=quotes_repo.current(),
            live_state=store.read_live_state(),
            universe_day=universe_days[-1] if universe_days else None, universe=universe,
            bar_counts=Counter(v.get("status", "?") for v in bar_status.values()),
            bar_symbols=len(bar_status),
            bar_files=sum(1 for _ in store.bars_dir.glob("*.parquet")) if store.bars_dir.exists() else 0,
            last_run=_read_json(settings.log_dir / "bars_last_run.json"),
            audit=_read_json(settings.log_dir / "bars_audit.json"))

    @app.get("/api/scan")
    def api_scan(request: Request, limit: int = 100):
        view, live = current()
        if view is None:
            return JSONResponse({"error": "no scan yet; run: run.py --scan"}, status_code=503)
        query = parse_query(request.query_params, rules)
        result = apply(with_live_prices(view, live), query, per_page=max(1, min(limit, 5000)),
                       crossings=live.crossings if live else None)
        return JSONResponse(fmt.clean({
            "scan_session": view.day, "universe_day": view.summary.get("universe_day"),
            "rules_digest": view.summary.get("rules_digest"),
            "rules_changed_since_scan": view.summary.get("rules_digest") != rules.digest,
            "note": NOT_ADVICE,
            "live": live_summary(live),
            "filters": query.params(), "errors": list(query.errors),
            "total": result.total, "page": result.page, "pages": result.pages,
            "results": [{**{k: row.get(k) for k in API_COLUMNS},
                         "live": bool(row.get("live", False)), "last_close": row.get("last_close"),
                         "url": symbol_url(row["symbol"]), "detections": row["matches"],
                         "crossings": row["crossings"]}
                        for row in result.rows],
        }))

    @app.get("/api/stamp")
    def api_stamp():
        return JSONResponse({"stamp": stamp()})

    @app.get("/api/channels")
    def api_channels():
        view = repo.current()
        fresh = channel_repo.fresh()
        return JSONResponse({"stamp": stamp(), "note": NOT_ADVICE + " The authors are AI agents.",
                             "channels": [{"id": c.id, "name": c.name, "count": c.count,
                                           "url": "/" if c.id == GENERAL else f"/c/{c.id}",
                                           "new_today": c.id in fresh}
                                          for c in channel_repo.channel_list(view)]})

    @app.get("/api/channels/{channel_id}")
    def api_channel(channel_id: str):
        channel_id = channel_id.strip().lower()
        if not channel_repo.known(channel_id):
            return JSONResponse({"error": f"no channel {channel_id}"}, status_code=404)
        threads = [{**{k: t.get(k) for k in ("id", "symbol", "pattern", "status", "live", "created_at")},
                    "day": t["day"],
                    "posts": [{"author": p["who"]["name"], "ai_agent": True, "kind": p["kind"],
                               "reply_to": p["reply_to"], "text": p["text"], "cites": p["cites"],
                               "drawings": p["drawings"], "has_chart": bool(p.get("svg"))}
                              for p in t["posts"]]}
                   for t in channel_repo.threads(channel_id)]
        return JSONResponse(fmt.clean({"channel": channel_id, "note": NOT_ADVICE + " The authors are AI agents.",
                                       "threads": threads}))

    @app.get("/api/live")
    def api_live():
        view, live = current()
        quotes = quotes_repo.current()
        return JSONResponse(fmt.clean({
            **live_summary(live),
            "fetched_at": quotes.summary.get("fetched_at") if quotes else None}))

    @app.get("/api/symbol/{symbol}")
    def api_symbol(symbol: str):
        view = repo.current()
        symbol = symbol.strip().upper()
        stock = view.stock(symbol) if view is not None and SYMBOL.match(symbol) else None
        if stock is None:
            return JSONResponse({"error": f"{symbol} is not in the newest scan"}, status_code=404)
        return JSONResponse(fmt.clean({
            "scan_session": view.day, "note": NOT_ADVICE,
            "stock": {k: stock.get(k) for k in API_COLUMNS},
            "detections": view.detections_of(symbol),
            "tradingview": tradingview_url(symbol),
        }))

    return app
