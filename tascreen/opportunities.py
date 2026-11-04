"""Opportunities (owner, 2026-11-04: "the filter should send me stocks, and put them on the
watchlist, that are not stretched but the ones that make you the opportunity; send more"):
the stocks that pass the money-flow filter (tascreen/flow.py) and are back on a rising average
instead of running far above it.

A stock is one when, at its last close:
- it passes the money-flow filter (the owner's strict one: template, steady, rating, volume,
  a leading group);
- it is not stretched: at most `opp_max_over_50_atr` ATRs over its 50-day average (the
  analyst's own stretch line, analyst/rules.yaml `stretch_atr`);
- it stands on a rising average: the 20-day (the close on or over it, at most
  `opp_max_over_20_atr` ATRs over), else the 50-day (the close on or over it); rising: above
  its value `opp_rise_sessions` sessions back;
- it came back to it: a low of the last `opp_touch_sessions` sessions within `opp_touch_atr`
  ATRs of that average;
- its exit is near: a close more than `support_break_atr` ATRs (analyst rules) under the
  average, at most `opp_max_risk_pct` under tonight's close. The same rule takes it off the
  TradingView list (setups_list.review).

Why (a study of 2026-11-04 on the followed stocks' stored bars, about 16 months, every fifth
session; today's stocks only, so survivors; without the group and volume checks; in our own
words): among the strong and steady uptrends, the ones meeting this rule, held for 20 sessions,
made about what the rest made (a median of about 2% against 1.8%). With the exit they made
less a trade (about 2.1% against 2.9%, the rest on the 20-day's exit) on a stop less than half
as far (about 4.4% against 9.8%), so more per unit of risk (0.49 of the risk against 0.30).
About 6 in 10 reached the exit within the 20 sessions. A quiet pullback, an up day and a tight
base under the high were no better, so none is asked for. Not advice.

Every evening after the report (run._opportunities): the best `opp_per_night` new ones (the
highest rating first, at most `opp_per_industry` an industry, none sent as one in the last
`opp_repeat_sessions` sessions nor sent tonight as a pick or an alert) go to the group: one
message with the list, then a chart each (candles, the 20- and 50-day averages, the exit); and
into the bot's TradingView list at once (kind "opp", standing on its average).
"""
from __future__ import annotations

import html
import logging
import math
from datetime import date
from typing import Any, Callable

import numpy as np
import pandas as pd

from .alerts import _link, _price
from .indicators import atr as atr_series

log = logging.getLogger(__name__)

MIN_BARS = 60
STUDY_NOTE = ("בבדיקה על כ-16 החודשים האחרונים (רק מניות שעדיין נסחרות, אז התוצאה מחמיאה): על כל שקל סיכון "
              "הן הרוויחו בערך פי 1.6 משאר המניות החזקות, אבל כ-6 מכל 10 הגיעו לנקודת היציאה בתוך חודש. "
              "לא ייעוץ השקעות.")


def of(bars: pd.DataFrame | None, cfg) -> dict[str, Any] | None:
    """The stock's opportunity at its last close (oldest bars first), or None: {"n" (20 or 50),
    "average", "atr", "close", "over_atr"}."""
    if bars is None or len(bars) < MIN_BARS:
        return None
    c, low = bars["close"].to_numpy(float), bars["low"].to_numpy(float)
    atr, close = float(atr_series(bars, 14).iloc[-1]), float(c[-1])
    if not (math.isfinite(atr) and atr > 0 and math.isfinite(close)):
        return None
    k = cfg.opp_rise_sessions
    avg = {n: (float(np.mean(c[-n:])), float(np.mean(c[-n - k:-k]))) for n in (20, 50)}
    if (close - avg[50][0]) / atr > cfg.opp_max_over_50_atr:
        return None                                     # stretched
    for n, limit in ((20, cfg.opp_max_over_20_atr), (50, cfg.opp_max_over_50_atr)):
        now, before = avg[n]
        over = (close - now) / atr
        if now > before and 0 <= over <= limit:
            break
    else:
        return None                                     # on no rising average
    if float(np.min(low[-cfg.opp_touch_sessions:])) > now + cfg.opp_touch_atr * atr:
        return None                                     # it never came back to it
    return {"n": n, "average": now, "atr": atr, "close": close, "over_atr": round(over, 2)}


def find(snap: dict[str, Any], bars_of: Callable[[str], pd.DataFrame | None], cfg, *,
         break_atr: float) -> list[dict[str, Any]]:
    """Every opportunity among tonight's passing stocks, the highest rating first (then the
    nearest exit). Each also carries the map's ticker, groups, rating and distance from the
    high, its exit (`stop`) and how far under the close that is (`risk_pct`)."""
    stocks = snap.get("stocks") or {}
    out = []
    for symbol in snap.get("passing") or []:
        o = of(bars_of(symbol), cfg)
        if o is None:
            continue
        x = stocks.get(symbol) or {}
        stop = o["average"] - break_atr * o["atr"]
        if (o["close"] - stop) / o["close"] * 100 > cfg.opp_max_risk_pct:
            continue                                    # the exit is too far: no opportunity
        out.append({"symbol": symbol, "ticker": x.get("ticker") or symbol.split(":")[-1],
                    "industry": x.get("industry"), "sector": x.get("sector"), "rs": int(x.get("rs") or 0),
                    "from_high_pct": x.get("from_high_pct"), **o, "stop": stop,
                    "risk_pct": (o["close"] - stop) / o["close"] * 100})
    out.sort(key=lambda o: (-o["rs"], o["risk_pct"], o["symbol"]))
    return out


def choose(found: list[dict], *, skip: set[str], per_night: int, per_industry: int) -> list[dict]:
    """The night's best new ones: `found`'s order, none of `skip`, at most `per_industry` of
    an industry (its sector when it has none)."""
    chosen, per = [], {}
    for o in found:
        group = o.get("industry") or o.get("sector") or ""
        if o["symbol"] in skip or per.get(group, 0) >= per_industry:
            continue
        chosen.append(o)
        per[group] = per.get(group, 0) + 1
        if len(chosen) >= per_night:
            break
    return chosen


def saved(o: dict[str, Any]) -> dict[str, Any]:
    """What the night's record keeps of one (data/alerts/<day>.json, the private state repo)."""
    return {"n": o["n"], "average": round(o["average"], 4), "atr": round(o["atr"], 4),
            "stop": round(o["stop"], 4), "rs": o["rs"]}


# ------------------------------------------------------------------ the message
def _group(o: dict[str, Any]) -> str:
    from .web.labels import INDUSTRIES, SECTORS

    return INDUSTRIES.get(o.get("industry") or "") or SECTORS.get(o.get("sector") or "") or str(o.get("industry") or "")


def what(o: dict[str, Any]) -> str:
    high = o.get("from_high_pct")
    near = (f" · {abs(float(high)):.0f}% מתחת לשיא השנתי" if isinstance(high, (int, float)) and math.isfinite(high)
            and round(abs(high)) >= 1 else " · ליד השיא השנתי")
    return f"חזרה לממוצע {o['n']} העולה ונסגרה מעליו{near}"


def exit_line(o: dict[str, Any]) -> str:
    return f"יציאה: סגירה מתחת ל-{_price(o['stop'])} ({o['risk_pct']:.1f}% מתחת לסגירה)"


def caption(o: dict[str, Any]) -> str:
    """One chart's caption (HTML)."""
    return (f"<b>{html.escape(o['ticker'])}</b> · {html.escape(_group(o))} · חוזק יחסי {o['rs']}\n"
            f"{what(o)}\n{exit_line(o)}")


def message(day: date, chosen: list[dict], *, listed: bool) -> str:
    """The evening's list for the group (HTML). `listed`: all of them are on the bot's
    TradingView list (the line says so only then)."""
    lines = [f"🎯 <b>הזדמנויות · מניות חזקות שלא מתוחות · {day:%d/%m}</b>",
             "מניות שעוברות את מסנן זרימת הכסף (מגמה עולה יציבה, חוזק יחסי, ענף מוביל) וחזרו לממוצע עולה "
             "במקום לרוץ רחוק ממנו: נקודת היציאה קרובה, והסיכון קטן יותר."]
    for n, o in enumerate(chosen, 1):
        lines += ["", f"{n}. {_link(o['symbol'])} · {html.escape(_group(o))} · חוזק יחסי {o['rs']}",
                  f"{what(o)}", exit_line(o)]
    lines += ["", *(["📋 כולן נכנסו לרשימה של הבוט בטריידינגוויו."] if listed else []), f"<i>{STUDY_NOTE}</i>"]
    return "\n".join(lines)


# ------------------------------------------------------------------ the charts
def chart(bars: pd.DataFrame, o: dict[str, Any]) -> str:
    """The analyst's chart (SVG): the candles, the 20- and 50-day averages and the exit."""
    from .analyst.chart import render
    from .analyst.facts import Analysis

    close = bars["close"].astype(float)
    change = float(close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) > 1 and close.iloc[-2] > 0 else math.nan
    analysis = Analysis(symbol=o["symbol"], last_day=str(bars["timestamp"].iloc[-1])[:10], window_first=0,
                        facts={"atr": {"value": o["atr"]}, "change_1d_pct": {"value": change}},
                        drawings={"ma": {"type": "ma", "periods": [20, 50]},
                                  "exit": {"type": "level", "price": float(o["stop"]), "label": "יציאה"}})
    return render(bars, analysis, ["ma", "exit"], title=o["ticker"])


def photos(chosen: list[dict], bars_of: Callable[[str], pd.DataFrame | None],
           to_png: Callable[[str], bytes]) -> list[tuple[bytes, str]]:
    """A chart and its caption for each one that can be drawn (the list goes out anyway)."""
    out = []
    for o in chosen:
        bars = bars_of(o["symbol"])
        if bars is None or len(bars) < 2:
            continue
        try:
            out.append((to_png(chart(bars, o)), caption(o)))
        except Exception as exc:                       # a picture is never worth a lost list
            log.warning("opportunity chart for %s failed: %s", o["symbol"], type(exc).__name__)
    return out
