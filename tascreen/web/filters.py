"""Screener filters: parsed from the URL, applied to the newest scan.

The same parameters drive the page (`/`) and the API (`/api/scan`), so a
filtered page can be bookmarked and an agent can ask for the same thing.

A stock passes when it passes every stock filter (market cap, RSI, ...). If any
pattern filter is set (family, pattern, direction, status, within), it must
also have at least one detection that passes all of them; those detections are
the ones shown next to it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace
from typing import Any, Mapping
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from ..patterns.rules import Rules
from . import labels
from .data import ScanView

FAMILIES = ("any", "chart", "candle")
DIRECTIONS = ("bullish", "bearish")
STATUSES = ("forming", "breakout", "busted", "signal")
SIDES = ("above", "below")
CROSSES = ("golden", "death")
LIVE = ("cross",)

# sort key -> (column, default order, Hebrew label)
SORTS = {
    "market_cap": ("market_cap", "desc", "שווי שוק"),
    "symbol": ("ticker", "asc", "סימול"),
    "change_1d_pct": ("change_1d_pct", "desc", "שינוי יומי"),
    "change_20d_pct": ("change_20d_pct", "desc", "שינוי חודשי"),
    "rsi14": ("rsi14", "desc", "RSI"),
    "rel_volume": ("rel_volume", "desc", "נפח יחסי"),
    "pct_from_52w_high": ("pct_from_52w_high", "desc", "מרחק מהשיא"),
    "atr_pct": ("atr_pct", "desc", "ATR%"),
    "avg_dollar_volume_20d": ("avg_dollar_volume_20d", "desc", "מחזור דולרי"),
    "age": ("_age", "asc", "עדכניות התבנית"),
}
DEFAULT_SORT = "market_cap"

# numeric parameter -> (Hebrew label, low, high, integer)
NUMBERS = {
    "mcap_min": ("שווי שוק מינימלי (מיליארד $)", 0.0, None, False),
    "mcap_max": ("שווי שוק מרבי (מיליארד $)", 0.0, None, False),
    "within": ("בימי המסחר האחרונים", 1, 400, True),
    "rsi_min": ("RSI מינימלי", 0.0, 100.0, False),
    "rsi_max": ("RSI מרבי", 0.0, 100.0, False),
    "relvol_min": ("נפח יחסי מינימלי", 0.0, None, False),
    "near_high": ("עד % מתחת לשיא השנתי", 0.0, 100.0, False),
    "atr_min": ("ATR% מינימלי", 0.0, None, False),
    "atr_max": ("ATR% מרבי", 0.0, None, False),
    "dollar_vol_min": ("מחזור דולרי יומי מינימלי (מיליון $)", 0.0, None, False),
}


@dataclass(frozen=True)
class Query:
    q: str = ""
    sector: str = ""
    exchange: str = ""
    mcap_min: float | None = None
    mcap_max: float | None = None
    family: str = ""
    patterns: tuple[str, ...] = ()
    direction: str = ""
    statuses: tuple[str, ...] = ()
    within: int | None = None
    rsi_min: float | None = None
    rsi_max: float | None = None
    sma50: str = ""
    sma200: str = ""
    relvol_min: float | None = None
    near_high: float | None = None
    atr_min: float | None = None
    atr_max: float | None = None
    dollar_vol_min: float | None = None
    cross: str = ""
    live: str = ""                 # "cross": only stocks crossing a breakout level now
    sort: str = DEFAULT_SORT
    order: str = ""
    page: int = 1
    errors: tuple[str, ...] = field(default=(), compare=False)

    @property
    def pattern_filter(self) -> bool:
        return bool(self.family or self.patterns or self.direction or self.statuses
                    or self.within)

    @property
    def sort_order(self) -> str:
        return self.order or SORTS[self.sort][1]

    def params(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "errors" or value in (None, "", ()):
                continue
            if f.name == "patterns":
                out += [("pattern", p) for p in value]
            elif f.name == "statuses":
                out += [("status", s) for s in value]
            elif (f.name, value) in (("sort", DEFAULT_SORT), ("page", 1)):
                continue
            else:
                out.append((f.name, f"{value:g}" if isinstance(value, float) else str(value)))
        return out

    def url(self, path: str = "/", **changes: Any) -> str:
        """This query with some fields changed (page resets unless given)."""
        changes.setdefault("page", 1)
        query = urlencode(replace(self, **changes).params())
        return f"{path}?{query}" if query else path

    def sort_url(self, key: str) -> str:
        if self.sort == key:
            order = "asc" if self.sort_order == "desc" else "desc"
        else:
            order = SORTS[key][1]
        return self.url(sort=key, order="" if order == SORTS[key][1] else order)

    def active(self, rules: Rules) -> list[tuple[str, str]]:
        """(Hebrew description, URL without it) for each filter in use."""
        out = []
        if self.q:
            out.append((f"חיפוש: {self.q}", self.url(q="")))
        if self.sector:
            out.append((f"סקטור: {labels.sector(self.sector)}", self.url(sector="")))
        if self.exchange:
            out.append((f"בורסה: {self.exchange}", self.url(exchange="")))
        if self.family:
            name = "כל תבנית" if self.family == "any" else labels.FAMILY[self.family]
            out.append((f"משפחה: {name}", self.url(family="")))
        for key in self.patterns:
            spec = {**rules.chart, **rules.candle}.get(key)
            out.append((spec.name_he if spec else key,
                        self.url(patterns=tuple(p for p in self.patterns if p != key))))
        if self.direction:
            out.append((labels.DIRECTION[self.direction], self.url(direction="")))
        for status in self.statuses:
            out.append((labels.STATUS[status],
                        self.url(statuses=tuple(s for s in self.statuses if s != status))))
        for name in ("sma50", "sma200"):
            side = getattr(self, name)
            if side:
                out.append((f"{'מעל' if side == 'above' else 'מתחת ל'}-SMA{name[3:]}",
                            self.url(**{name: ""})))
        if self.cross:
            out.append(("חציית זהב" if self.cross == "golden" else "חציית מוות", self.url(cross="")))
        if self.live:
            out.append(("חוצות עכשיו קו פריצה", self.url(live="")))
        for name, (label, *_rest) in NUMBERS.items():
            value = getattr(self, name)
            if value is not None:
                out.append((f"{label}: {value:g}", self.url(**{name: None})))
        return out


def parse_query(params: Mapping, rules: Rules) -> Query:
    """Query from URL parameters (a Starlette QueryParams or any mapping with getlist).
    Unknown or malformed values are dropped and reported in `errors`, in Hebrew."""
    errors: list[str] = []
    known_patterns = {**rules.chart, **rules.candle}

    def one(name: str) -> str:
        return (params.get(name) or "").strip()

    def choice(name: str, allowed: tuple[str, ...], label: str) -> str:
        value = one(name)
        if value and value not in allowed:
            errors.append(f"{label}: הערך '{value}' לא מוכר, התעלמתי ממנו")
            return ""
        return value

    def many(name: str, allowed, label: str) -> tuple[str, ...]:
        values = [v.strip() for v in getlist(params, name) if v.strip()]
        bad = [v for v in values if v not in allowed]
        if bad:
            errors.append(f"{label}: {', '.join(bad)} לא מוכר, התעלמתי")
        return tuple(dict.fromkeys(v for v in values if v in allowed))

    numbers: dict[str, Any] = {}
    for name, (label, low, high, integer) in NUMBERS.items():
        raw = one(name)
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            value = math.nan
        if not math.isfinite(value) or value < low or (high is not None and value > high):
            limits = f"{low:g} עד {high:g}" if high is not None else f"{low:g} ומעלה"
            errors.append(f"{label}: '{raw}' לא בטווח ({limits}), התעלמתי")
            continue
        numbers[name] = int(value) if integer else value

    sort = one("sort") or DEFAULT_SORT
    if sort not in SORTS:
        errors.append(f"מיון: '{sort}' לא מוכר")
        sort = DEFAULT_SORT
    order = choice("order", ("asc", "desc"), "סדר")
    try:
        page = max(1, int(one("page") or 1))
    except ValueError:
        page = 1

    return Query(
        q=one("q")[:60],
        sector=one("sector"),
        exchange=one("exchange"),
        family=choice("family", FAMILIES, "משפחה"),
        patterns=many("pattern", known_patterns, "תבנית"),
        direction=choice("direction", DIRECTIONS, "כיוון"),
        statuses=many("status", STATUSES, "סטטוס"),
        sma50=choice("sma50", SIDES, "SMA50"),
        sma200=choice("sma200", SIDES, "SMA200"),
        cross=choice("cross", CROSSES, "חצייה"),
        live=choice("live", LIVE, "מחירים חיים"),
        sort=sort, order=order, page=page,
        errors=tuple(errors),
        **numbers,
    )


def getlist(params: Mapping, name: str) -> list[str]:
    if hasattr(params, "getlist"):
        return list(params.getlist(name))
    value = params.get(name)
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


@dataclass(frozen=True)
class Result:
    rows: list[dict[str, Any]]     # this page; each has "matches" (detection dicts)
    total: int                     # stocks passing, over all pages
    detections: int                # detections shown with those stocks
    page: int
    pages: int


def _stock_mask(s: pd.DataFrame, q: Query) -> pd.Series:
    m = pd.Series(True, index=s.index)
    if q.q:
        needle = q.q.lower()
        m &= (s["symbol"].str.lower().str.contains(needle, regex=False)
              | s["description"].fillna("").str.lower().str.contains(needle, regex=False))
    if q.sector:
        m &= s["sector"] == q.sector
    if q.exchange:
        m &= s["exchange"] == q.exchange
    if q.mcap_min is not None:
        m &= s["market_cap"] >= q.mcap_min * 1e9
    if q.mcap_max is not None:
        m &= s["market_cap"] <= q.mcap_max * 1e9
    if q.rsi_min is not None:
        m &= s["rsi14"] >= q.rsi_min
    if q.rsi_max is not None:
        m &= s["rsi14"] <= q.rsi_max
    for name in ("sma50", "sma200"):
        side = getattr(q, name)
        if side:     # above_smaN is True/False, or None when there are too few bars
            want = side == "above"
            m &= s[f"above_{name}"].map(lambda v: isinstance(v, (bool, np.bool_)) and bool(v) == want)
    if q.relvol_min is not None:
        m &= s["rel_volume"] >= q.relvol_min
    if q.near_high is not None:
        m &= s["pct_from_52w_high"] >= -q.near_high
    if q.atr_min is not None:
        m &= s["atr_pct"] >= q.atr_min
    if q.atr_max is not None:
        m &= s["atr_pct"] <= q.atr_max
    if q.dollar_vol_min is not None:
        m &= s["avg_dollar_volume_20d"] >= q.dollar_vol_min * 1e6
    if q.cross:
        m &= s[f"{q.cross}_cross_days_ago"].notna()
    return m


def _detection_mask(d: pd.DataFrame, q: Query) -> pd.Series:
    m = pd.Series(True, index=d.index)
    if q.family in ("chart", "candle"):
        m &= d["family"] == q.family
    if q.patterns:
        m &= d["pattern"].isin(q.patterns)
    if q.direction:
        m &= d["direction"] == q.direction
    if q.statuses:
        m &= d["status"].isin(q.statuses)
    if q.within:
        m &= d["age"] < q.within
    return m


MATCH_FIELDS = ("pattern", "name_he", "family", "direction", "status", "age", "event_day",
                "breakout_price", "target")


def apply(view: ScanView, q: Query, per_page: int,
          crossings: dict[str, list[dict]] | None = None) -> Result:
    """`crossings`: symbol -> live crossings (see web.data.live_for); with
    `live=cross` only those stocks pass, and every row carries its own."""
    crossings = crossings or {}
    stocks = view.stocks.loc[_stock_mask(view.stocks, q)]
    if q.live == "cross":
        stocks = stocks.loc[stocks["symbol"].isin(list(crossings))]
    det = view.detections
    matched = det.loc[_detection_mask(det, q)] if q.pattern_filter else det
    matched = matched.loc[matched["symbol"].isin(stocks["symbol"])]
    if q.pattern_filter:
        stocks = stocks.loc[stocks["symbol"].isin(matched["symbol"])]

    stocks = stocks.assign(_age=stocks["symbol"].map(matched.groupby("symbol")["age"].min()))
    column = SORTS[q.sort][0]
    stocks = stocks.sort_values([column, "market_cap"], ascending=[q.sort_order == "asc", False],
                                na_position="last", kind="stable")

    total = len(stocks)
    pages = max(1, math.ceil(total / per_page))
    page = min(q.page, pages)
    shown = stocks.iloc[(page - 1) * per_page: page * per_page]
    by_symbol: dict[str, list[dict]] = {}
    for row in matched.loc[matched["symbol"].isin(shown["symbol"]), ["symbol", *MATCH_FIELDS]] \
            .to_dict("records"):
        by_symbol.setdefault(row.pop("symbol"), []).append(row)
    rows = []
    for row in shown.drop(columns="_age").to_dict("records"):
        row["matches"] = by_symbol.get(row["symbol"], [])
        row["crossings"] = crossings.get(row["symbol"], [])
        rows.append(row)
    return Result(rows=rows, total=total, detections=int(len(matched)), page=page, pages=pages)
