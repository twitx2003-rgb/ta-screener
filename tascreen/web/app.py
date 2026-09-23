"""The website: FastAPI + Jinja2, Hebrew and right-to-left, on 127.0.0.1 only.

Pages:  /  (screener)   /symbol/{EXCHANGE:TICKER}   /patterns   /status
API:    /api/scan (same filters as /)   /api/symbol/{EXCHANGE:TICKER}

Everything is read from data/ (the newest scan, the stored bars) and logs/;
nothing here calls TradingView.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from ..config import Settings
from ..indicators import sma
from ..patterns.rules import Rules, load_rules
from ..store import Store
from . import fmt, labels
from .data import ScanRepository, ScanView
from .filters import SORTS, STATUSES, Query, apply, parse_query

HOST = "127.0.0.1"          # never configurable: TradingView data is for the account holder
WEB_DIR = Path(__file__).resolve().parent
SYMBOL = re.compile(r"^[A-Z]{1,12}:[A-Z0-9.\-_]{1,20}$")
NOT_ADVICE = ("Mechanical readings of past prices, not investment advice. A target is the "
              "book's measure rule applied to the pattern's height, not a forecast.")

API_COLUMNS = ("symbol", "ticker", "description", "exchange", "sector", "industry", "market_cap",
               "last_date", "close", "change_1d_pct", "change_5d_pct", "change_20d_pct",
               "rsi14", "sma20", "sma50", "sma200", "above_sma50", "above_sma200", "atr_pct",
               "rel_volume", "pct_from_52w_high", "pct_from_52w_low", "avg_dollar_volume_20d",
               "golden_cross_days_ago", "death_cross_days_ago", "next_earnings", "coverage",
               "patterns_skipped")


def symbol_url(symbol: str) -> str:
    return "/symbol/" + quote(symbol, safe=":")


def tradingview_url(symbol: str) -> str:
    return "https://www.tradingview.com/chart/?symbol=" + quote(symbol, safe="")


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
        "sma200": line(sma(bars["close"], 200)),
        "detections": [{
            "id": i, "family": d["family"], "pattern": d["pattern"], "name": d["name_he"],
            "direction": d["direction"], "status": d["status"],
            "start": _iso(d["start"]), "end": _iso(d["end"]),
            "breakout_date": _iso(d["breakout_date"]),
            "breakout_price": d["breakout_price"], "target": d["target"],
            "points": d["points"], "lines": d["lines"],
        } for i, d in enumerate(detections)],
    }


def create_app(settings: Settings, *, rules: Rules | None = None) -> FastAPI:
    store = Store(settings.data_dir)
    rules = rules or load_rules()
    repo = ScanRepository(store, rules)
    specs = {**rules.chart, **rules.candle}

    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    templates.env.filters.update(
        num=fmt.num, price=fmt.price, pct=fmt.pct, money=fmt.money, day=fmt.day,
        sign=fmt.sign_class, check_text=labels.check_text, sector=labels.sector,
        symbol_url=symbol_url, tradingview_url=tradingview_url)
    templates.env.globals.update(labels=labels, rules=rules, specs=specs, SORTS=SORTS,
                                 STATUSES=STATUSES, ok=fmt.ok)

    app = FastAPI(title="ta-screener", docs_url=None, redoc_url=None, openapi_url=None)
    # Only local names: a page on another site cannot reach this server through a
    # rebinding DNS name.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost"])
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    def render(request: Request, name: str, view: ScanView | None, status_code: int = 200,
               **context: Any) -> HTMLResponse:
        stale_rules = bool(view and view.summary.get("rules_digest") != rules.digest)
        return templates.TemplateResponse(
            request, name, {"view": view, "stale_rules": stale_rules, "path": request.url.path,
                            **context}, status_code=status_code)

    def not_found(request: Request, view: ScanView | None, what: str) -> HTMLResponse:
        return render(request, "notfound.html", view, status_code=404, what=what)

    @app.get("/", response_class=HTMLResponse)
    def screener(request: Request):
        view = repo.current()
        if view is None:
            return render(request, "empty.html", None)
        query = parse_query(request.query_params, rules)
        result = apply(view, query, settings.web.rows_per_page)
        stocks = view.stocks
        facets = {
            "sectors": sorted(((labels.sector(s), s) for s in stocks["sector"].dropna().unique()
                               if s), key=lambda pair: pair[0]),
            "exchanges": sorted(stocks["exchange"].dropna().unique()),
            "counts": view.detections["pattern"].value_counts().to_dict(),
        }
        return render(request, "screener.html", view, query=query, result=result, facets=facets)

    @app.get("/symbol/{symbol}", response_class=HTMLResponse)
    def symbol_page(request: Request, symbol: str):
        view = repo.current()
        symbol = symbol.strip().upper()
        stock = view.stock(symbol) if view is not None and SYMBOL.match(symbol) else None
        if stock is None:
            return not_found(request, view, symbol)
        detections = view.detections_of(symbol)
        bars = store.read_bars(symbol)
        chart = fmt.script_json(chart_payload(bars, detections)) if bars is not None else None
        return render(request, "symbol.html", view, stock=stock, detections=detections,
                      chart=chart, back=request.headers.get("referer", ""))

    @app.get("/patterns", response_class=HTMLResponse)
    def patterns_page(request: Request):
        view = repo.current()
        counts = view.detections["pattern"].value_counts().to_dict() if view is not None else {}
        return render(request, "patterns.html", view, counts=counts)

    @app.get("/status", response_class=HTMLResponse)
    def status_page(request: Request):
        view = repo.current()
        bar_status = store.read_status()
        universe_days = store.universe_days()
        universe = (_read_json(store.universe_dir / f"{universe_days[-1].isoformat()}.json")
                    if universe_days else None)
        return render(
            request, "status.html", view,
            universe_day=universe_days[-1] if universe_days else None, universe=universe,
            bar_counts=Counter(v.get("status", "?") for v in bar_status.values()),
            bar_symbols=len(bar_status),
            bar_files=sum(1 for _ in store.bars_dir.glob("*.parquet")) if store.bars_dir.exists() else 0,
            last_run=_read_json(settings.log_dir / "bars_last_run.json"),
            audit=_read_json(settings.log_dir / "bars_audit.json"))

    @app.get("/api/scan")
    def api_scan(request: Request, limit: int = 100):
        view = repo.current()
        if view is None:
            return JSONResponse({"error": "no scan yet; run: run.py --scan"}, status_code=503)
        query = parse_query(request.query_params, rules)
        result = apply(view, query, per_page=max(1, min(limit, 5000)))
        return JSONResponse(fmt.clean({
            "scan_session": view.day, "universe_day": view.summary.get("universe_day"),
            "rules_digest": view.summary.get("rules_digest"),
            "rules_changed_since_scan": view.summary.get("rules_digest") != rules.digest,
            "note": NOT_ADVICE,
            "filters": query.params(), "errors": list(query.errors),
            "total": result.total, "page": result.page, "pages": result.pages,
            "results": [{**{k: row.get(k) for k in API_COLUMNS},
                         "url": symbol_url(row["symbol"]), "detections": row["matches"]}
                        for row in result.rows],
        }))

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
