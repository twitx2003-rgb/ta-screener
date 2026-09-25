"""One analysis: every level and signal of one stock, as facts and drawings with ids.

`analyse(bars, symbol)` -> Analysis:
- facts: {key: {value, label, unit}} (the same shape as the channels' briefs), all that
  a text about the stock may quote; the qualitative ones too (trend state, RSI zone,
  divergence kind, volume trend), so words can be checked like numbers;
- drawings: {id: {...}} with exact dates and prices, for the chart, the Pine Script and
  the model's choice (zone_1, tl_1, fib, div_1, vp, ma, pat_1, ...).
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..indicators import atr as atr_series
from ..patterns.candles import detect_candles
from ..patterns.chart import detect_chart
from ..patterns.levels import invalidation
from ..patterns.rules import Rules, load_rules as load_pattern_rules
from . import load_rules
from .signals import divergences, ma_stack, macd, rsi_series, volume_profile, volume_trend
from .zones import fibonacci, pivots, sr_zones, trendlines

KIND_HE = {"support": "תמיכה", "resistance": "התנגדות"}
STACK_HE = {"bullish": "שורי (50 מעל 150 מעל 200, המחיר מעליהם)",
            "bearish": "דובי (50 מתחת ל-150 מתחת ל-200, המחיר מתחתיהם)",
            "mixed": "מעורב", "unknown": "לא ידוע (אין מספיק היסטוריה)"}
TREND_HE = {"rising": "עולה", "falling": "יורד", "flat": "יציב"}
STATUS_HE = {"forming": "בבנייה", "breakout": "פריצה", "busted": "פריצה כושלת", "signal": "אות נר"}
DIRECTION_HE = {"bullish": "שורי", "bearish": "דובי", "either": "כיוון לא ידוע עדיין"}


@dataclass
class Analysis:
    symbol: str
    last_day: str
    window_first: int                  # index into the bars where the analysed window starts
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    drawings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


def _num(x: Any, digits: int = 2) -> float | None:
    try:
        value = float(x)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


class _Facts:
    def __init__(self):
        self.out: dict[str, dict[str, Any]] = {}

    def add(self, key: str, value: Any, label: str, unit: str = "", digits: int = 2) -> None:
        if isinstance(value, (int, float, np.floating, np.integer)) and not isinstance(value, bool):
            value = _num(value, digits)
            if value is None:
                return
            if digits == 0:
                value = int(value)
        if value is None or value == "":
            return
        self.out[key] = {"value": value, "label": label, "unit": unit}


def _pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if b else math.nan


def _day(bars: pd.DataFrame, i: int) -> str:
    return bars["timestamp"].iloc[i].date().isoformat()


def analyse(bars: pd.DataFrame, symbol: str, *, rules: dict[str, Any] | None = None,
            pattern_rules: Rules | None = None) -> Analysis:
    rules = rules or load_rules()
    pattern_rules = pattern_rules or load_pattern_rules()
    bars = bars.reset_index(drop=True)
    last = len(bars) - 1
    first = max(0, len(bars) - int(rules["window_sessions"]))
    window = bars.iloc[first:]
    close = bars["close"]
    atr = atr_series(bars, int(rules["atr_period"])).to_numpy()
    atr_now = float(atr[-1]) if len(atr) else math.nan
    price = float(close.iloc[-1])
    result = Analysis(symbol, _day(bars, last), first)
    f, d = _Facts(), result.drawings

    f.add("close", price, "סגירה אחרונה", "$")
    f.add("last_date", _day(bars, last), "יום המסחר האחרון")
    if last >= 1:
        f.add("change_1d_pct", _pct(price, float(close.iloc[-2])), "השינוי ביום המסחר האחרון", "%", 2)
    if last >= 20:
        f.add("change_20d_pct", _pct(price, float(close.iloc[-21])), "שינוי ב-20 ימי מסחר", "%", 1)
    f.add("atr", atr_now, "ATR (תנודה יומית ממוצעת, 14)", "$")
    # the 52-week extremes (review round 1: a stock 2% under its high was "without
    # resistance"; a stock at a new high had "no level")
    year = bars.iloc[max(0, last - 251):]
    hi_i = int(year["high"].to_numpy(float).argmax()) + max(0, last - 251)
    lo_i = int(year["low"].to_numpy(float).argmin()) + max(0, last - 251)
    high52, low52 = float(bars["high"].iloc[hi_i]), float(bars["low"].iloc[lo_i])
    f.add("high_52w", high52, "השיא של 52 השבועות", "$")
    f.add("high_52w_day", _day(bars, hi_i), "יום השיא השנתי")
    f.add("high_52w_distance_pct", max(0.0, (1 - price / high52) * 100), "כמה הסגירה מתחת לשיא השנתי", "%", 1)
    f.add("low_52w", low52, "השפל של 52 השבועות", "$")
    f.add("low_52w_day", _day(bars, lo_i), "יום השפל השנתי")
    f.add("low_52w_distance_pct", max(0.0, (price / low52 - 1) * 100), "כמה הסגירה מעל השפל השנתי", "%", 1)

    # moving averages
    stack = ma_stack(close, list(rules["ma_periods"]))
    for n, value in stack["values"].items():
        f.add(f"sma{n}", value, f"ממוצע {n} יום", "$")
        if value is not None and math.isfinite(value):
            f.add(f"vs_sma{n}_pct", _pct(price, value), f"המרחק מממוצע {n}", "%", 1)
    f.add("ma.stack", STACK_HE[stack["state"]], "סדר הממוצעים")
    d["ma"] = {"type": "ma", "periods": list(rules["ma_periods"])}

    # RSI and MACD
    rsi_values = rsi_series(close, int(rules["rsi_period"]))
    rsi_now = float(rsi_values.iloc[-1])
    f.add("rsi14", rsi_now, "RSI 14", "", 1)
    if math.isfinite(rsi_now):
        zone = ("קניית יתר" if rsi_now >= rules["rsi_overbought"] else
                "מכירת יתר" if rsi_now <= rules["rsi_oversold"] else "ניטרלי")
        f.add("rsi.zone", zone, f"אזור ה-RSI (מעל {rules['rsi_overbought']} / מתחת {rules['rsi_oversold']})")
    fast, slow, signal = rules["macd"]
    line, sig, hist = macd(close, fast, slow, signal)
    f.add("macd", float(line.iloc[-1]), "MACD", "", 3)
    f.add("macd.signal", float(sig.iloc[-1]), "קו האות של MACD", "", 3)
    f.add("macd.hist", float(hist.iloc[-1]), "היסטוגרמת MACD", "", 3)
    if math.isfinite(float(hist.iloc[-1])):
        f.add("macd.state", "מעל קו האות" if hist.iloc[-1] > 0 else "מתחת לקו האות", "מצב ה-MACD")
        f.add("macd.zero", "מעל 0" if line.iloc[-1] > 0 else "מתחת ל-0", "MACD ביחס ל-0")

    # turning points, zones, trendlines, Fibonacci, divergences
    minor = [p for p in pivots(bars, atr, rules["minor_pivot_atr"]) if p.i >= first]
    major = [p for p in pivots(bars, atr, rules["major_pivot_atr"]) if p.i >= first]
    for zone in sr_zones(window.reset_index(drop=True), [_shift(p, first) for p in minor], atr_now, rules):
        key = zone.id
        f.add(f"{key}.kind", KIND_HE[zone.kind], f"סוג האזור {key}")
        f.add(f"{key}.low", zone.low, f"גבול תחתון של {key}", "$")
        f.add(f"{key}.high", zone.high, f"גבול עליון של {key}", "$")
        f.add(f"{key}.touches", zone.touches, f"מספר הנגיעות ב-{key}", "", 0)
        # to the zone's near edge, 0 inside it (as level.* do: one distance per zone)
        edge = zone.low if zone.kind == "resistance" else zone.high
        near = max(0.0, _pct(edge, price) if zone.kind == "resistance" else -_pct(edge, price))
        f.add(f"{key}.distance_pct", near, f"המרחק מהסגירה אל {key}", "%", 1)
        d[key] = {"type": "zone", "kind": zone.kind, "low": zone.low, "high": zone.high,
                  "first_day": zone.days[0], "last_day": zone.days[-1], "touches": zone.touches}
    for tl in trendlines(bars, minor, atr, rules):
        key, now_value = tl.id, tl.at(last)
        slope10 = _pct(tl.at(last), tl.at(last - 10)) if last >= 10 else math.nan
        f.add(f"{key}.kind", KIND_HE[tl.kind], f"סוג קו המגמה {key}")
        f.add(f"{key}.value", now_value, f"קו המגמה {key} ביום האחרון", "$")
        f.add(f"{key}.touches", tl.touches, f"נגיעות בקו {key}", "", 0)
        f.add(f"{key}.slope_pct", slope10, f"שיפוע {key} (אחוז ל-10 ימים)", "%", 2)
        f.add(f"{key}.direction", "עולה" if slope10 > 0.3 else "יורד" if slope10 < -0.3 else "שטוח",
              f"כיוון קו המגמה {key}")
        f.add(f"{key}.since", tl.day1, f"קו המגמה {key} מתחיל ב")
        f.add(f"{key}.distance_pct", _pct(now_value, price), f"המרחק מהמחיר אל {key}", "%", 1)
        d[key] = {"type": "line", "kind": tl.kind, "day1": tl.day1, "price1": round(tl.at(tl.i1), 4),
                  "day2": _day(bars, last), "price2": round(now_value, 4), "touches": tl.touches}
    # the big turning points, as levels the scenarios can name (review round 1: a June low
    # the price bounced from was not a level because it was touched once)
    d["swings"] = {"type": "swings", "points": [
        {"day": _day(bars, p.i), "price": round(float(p.price), 4), "kind": p.kind} for p in major]}
    fib = fibonacci(bars, major, rules, atr_now)
    if fib is not None:
        f.add("fib.direction", "עלייה" if fib.direction == "up" else "ירידה", "כיוון התנועה הגדולה האחרונה")
        f.add("fib.start", fib.start, "תחילת התנועה", "$")
        f.add("fib.end", fib.end, "סוף התנועה", "$")
        f.add("fib.start_day", fib.start_day, "תאריך תחילת התנועה")
        f.add("fib.end_day", fib.end_day, "תאריך סוף התנועה")
        f.add("fib.retraced_pct", fib.position * 100, "כמה מהתנועה המחיר כבר תיקן", "%", 1)
        for key, value in fib.levels.items():
            ratio = int(key.rsplit("_", 1)[1]) / 10
            label = (f"הרחבת פיבונאצ'י {ratio:g}%" if "ext" in key else f"תיקון פיבונאצ'י {ratio:g}%")
            f.add(key, value, label, "$")
        d["fib"] = {"type": "fib", "direction": fib.direction, "start_day": fib.start_day,
                    "end_day": fib.end_day, "start": fib.start, "end": fib.end, "levels": fib.levels}
    for div in divergences(bars, minor, rsi_values, line, int(rules["divergence_recent_sessions"])):
        key = div.id
        f.add(f"{key}.kind", "שורית" if div.kind == "bullish" else "דובית", f"סוג הסטייה {key}")
        f.add(f"{key}.indicator", div.indicator.upper(), f"המתנד בסטייה {key}")
        f.add(f"{key}.day1", div.day1, f"הנקודה הראשונה של {key}")
        f.add(f"{key}.day2", div.day2, f"הנקודה השנייה של {key}")
        second = int((bars["timestamp"].dt.strftime("%Y-%m-%d") <= div.day2).sum()) - 1
        f.add(f"{key}.sessions_ago", last - second, f"כמה ימי מסחר עברו מהנקודה השנייה של {key}", "", 0)
        f.add(f"{key}.price_since_pct", _pct(price, float(div.price2)),
              f"השינוי במחיר מאז הנקודה השנייה של {key}", "%", 1)
        d[key] = {"type": "divergence", "kind": div.kind, "indicator": div.indicator,
                  "day1": div.day1, "price1": div.price1, "day2": div.day2, "price2": div.price2}

    # volume
    profile = volume_profile(window, int(rules["volume_profile_bins"]), float(rules["value_area"]))
    if profile is not None:
        f.add("vp.poc", profile["poc"], "מחיר השליטה (POC) בפרופיל הנפח", "$")
        f.add("vp.val", profile["val"], "תחתית אזור הערך (70% מהנפח)", "$")
        f.add("vp.vah", profile["vah"], "ראש אזור הערך (70% מהנפח)", "$")
        d["vp"] = {"type": "profile", **profile}
    vt = volume_trend(bars["volume"], int(rules["volume_trend_short"]), int(rules["volume_trend_long"]))
    f.add("volume.ratio", vt["ratio"], "נפח ממוצע 20 יום חלקי 50 יום", "x", 2)
    f.add("volume.trend", TREND_HE[vt["trend"]], "מגמת הנפח")

    # Bulkowski patterns (the scanner's own detectors), recent ones only
    specs = {**pattern_rules.chart, **pattern_rules.candle}
    found = detect_chart(bars, symbol, pattern_rules) + detect_candles(bars, symbol, pattern_rules)
    for n, det in enumerate(found, 1):
        key, record = f"pat_{n}", det.row() | {"points": det.points, "lines": det.lines}
        spec = specs.get(det.pattern)
        f.add(f"{key}.name", spec.name_he if spec else det.pattern, f"התבנית {key}")
        f.add(f"{key}.status", STATUS_HE.get(det.status, det.status), f"מצב {key}")
        f.add(f"{key}.direction", DIRECTION_HE.get(det.direction, det.direction), f"כיוון {key}")
        if det.breakout_date is not None:
            f.add(f"{key}.breakout_day", det.breakout_date.date().isoformat(), f"יום הפריצה של {key}")
        f.add(f"{key}.breakout", det.breakout_price, f"קו הפריצה של {key}", "$")
        if det.status == "breakout" and det.breakout_date is not None and det.direction in ("bullish", "bearish"):
            # where the price is now against the breakout (review round 1: a breakdown the
            # price had already closed back above was told as live)
            level, up = float(det.breakout_price), det.direction == "bullish"
            since = int((bars["timestamp"] > det.breakout_date).sum())
            back = price < level if up else price > level
            f.add(f"{key}.sessions_since_breakout", since, f"ימי מסחר מאז הפריצה של {key}", "", 0)
            f.add(f"{key}.close_vs_breakout_pct", _pct(price, level), f"הסגירה ביחס לקו הפריצה של {key}", "%", 1)
            f.add(f"{key}.state", ("המחיר חזר אל תוך התבנית: הפריצה מוטלת בספק" if back else
                                   "המחיר מעל קו הפריצה" if up else "המחיר מתחת לקו השבירה"),
                  f"מצב הפריצה של {key} היום")
        f.add(f"{key}.target", det.target, f"יעד {key} לפי כלל המדידה (לא תחזית)", "$")
        f.add(f"{key}.invalidation", invalidation(record, bars), f"רמת הביטול של {key}", "$")
        d[key] = {"type": "pattern", "pattern": det.pattern, "family": det.family,
                  "status": det.status, "direction": det.direction,
                  "start": det.start.date().isoformat(), "end": det.end.date().isoformat(),
                  "breakout": _num(det.breakout_price, 4), "target": _num(det.target, 4),
                  "breakout_day": det.breakout_date.date().isoformat() if det.breakout_date is not None else None,
                  "invalidation": _num(invalidation(record, bars), 4),
                  "points": det.points, "lines": det.lines}
    result.facts = f.out
    # the levels the chart shows and the scenarios on them, as facts (view.py)
    from .view import key_level_facts
    result.facts.update(key_level_facts(result, rules))
    return result


def _shift(p, first: int):
    """A turning point with its index moved into the window."""
    from ..patterns.pivots import Pivot
    return Pivot(p.i - first, p.price, p.kind)
