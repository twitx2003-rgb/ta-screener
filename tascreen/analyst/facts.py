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
from ..indicators import sma
from ..patterns.candles import detect_candles
from ..patterns.chart import detect_chart
from ..patterns.levels import invalidation
from ..patterns.rules import Rules, load_rules as load_pattern_rules
from . import load_rules
from .signals import divergences, ma_stack, macd, rsi_series, volume_profile, volume_trend
from .zones import fibonacci, pivots, sr_zones, trendlines

KIND_HE = {"support": "תמיכה", "resistance": "התנגדות"}
# plain words (review round 2: "the averages' order is mixed" told a reader nothing)
STACK_HE = {"bullish": "מגמת עלייה: המחיר מעל הממוצעים של 50, 150 ו-200 יום, והם מסודרים 50 מעל 150 מעל 200 (שורי)",
            "bearish": "מגמת ירידה: המחיר מתחת לממוצעים של 50, 150 ו-200 יום, והם מסודרים 50 מתחת ל-150 מתחת ל-200 (דובי)",
            "mixed": "אין מגמה ברורה: הממוצעים של 50, 150 ו-200 יום לא מסודרים בכיוון אחד",
            "unknown": "לא ידוע (אין מספיק היסטוריה)"}
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


def _volume_ratio(bars: pd.DataFrame, i: int, sessions: int = 50) -> float:
    """Session i's volume over the average of the `sessions` before it."""
    before = bars["volume"].iloc[max(0, i - sessions):i].to_numpy(float)
    avg = float(np.nanmean(before)) if len(before) else math.nan
    return float(bars["volume"].iloc[i]) / avg if avg and math.isfinite(avg) else math.nan


def _broken_line_now(lines: list[dict], level: float, index: dict[str, int], at_i: int, last: int) -> float:
    """The pattern line the breakout crossed (the one worth `level` on the breakout
    session), extended to the last session. A wedge's or triangle's line keeps its slope:
    review round 2 found a close measured against the flat breakout price called
    "back inside" while it was still under the rising line (a retest from below)."""
    best, best_gap = None, math.inf
    for line in lines or []:
        i1, i2 = index.get(str(line.get("x1"))), index.get(str(line.get("x2")))
        if i1 is None or i2 is None or i2 == i1:
            continue
        slope = (float(line["y2"]) - float(line["y1"])) / (i2 - i1)
        value = float(line["y1"]) + slope * (at_i - i1)
        if abs(value - level) < best_gap:
            best, best_gap = (float(line["y1"]), slope, i1), abs(value - level)
    if best is None:
        return level
    y1, slope, i1 = best
    return y1 + slope * (last - i1)


def _zone_break(close: np.ndarray, low: float, high: float, last: int, fresh: int) -> tuple[str, int] | None:
    """A zone the price closed through in the last `fresh` sessions and has not closed
    back into since: ("up", session) or ("down", session)."""
    price = close[last]
    for j in range(last, max(0, last - fresh), -1):
        before = close[max(0, j - 5):j]           # it came from the other side, not from inside
        if price > high and close[j] > high >= close[j - 1] and (before < low).any():
            return ("up", j) if (close[j:last + 1] > high).all() else None
        if price < low and close[j] < low <= close[j - 1] and (before > high).any():
            return ("down", j) if (close[j:last + 1] < low).all() else None
    return None


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
    f.add("high_52w_sessions_ago", last - hi_i, "לפני כמה ימי מסחר נקבע השיא השנתי", "", 0)
    f.add("low_52w", low52, "השפל של 52 השבועות", "$")
    f.add("low_52w_day", _day(bars, lo_i), "יום השפל השנתי")
    f.add("low_52w_distance_pct", max(0.0, (price / low52 - 1) * 100), "כמה הסגירה מעל השפל השנתי", "%", 1)
    f.add("low_52w_sessions_ago", last - lo_i, "לפני כמה ימי מסחר נקבע השפל השנתי", "", 0)
    f.add("volume.last_ratio", _volume_ratio(bars, last), "הנפח ביום האחרון חלקי ממוצע 50 הימים שלפניו", "x", 2)

    # moving averages
    stack = ma_stack(close, list(rules["ma_periods"]))
    for n, value in stack["values"].items():
        f.add(f"sma{n}", value, f"ממוצע {n} יום", "$")
        if value is not None and math.isfinite(value):
            f.add(f"vs_sma{n}_pct", _pct(price, value), f"המרחק מממוצע {n}", "%", 1)
    f.add("ma.stack", STACK_HE[stack["state"]], "סדר הממוצעים")
    known_ma = [v for v in stack["values"].values() if v is not None and math.isfinite(v)]
    if known_ma:
        f.add("ma.price_vs", ("המחיר מעל כל הממוצעים" if price > max(known_ma) else
                              "המחיר מתחת לכל הממוצעים" if price < min(known_ma) else "המחיר בין הממוצעים"),
              "המחיר ביחס לממוצעים של 50, 150 ו-200 יום")
    for n in stack["values"]:
        line_n = sma(close, n)
        if last >= 10 and math.isfinite(float(line_n.iloc[-11])):
            slope = _pct(float(line_n.iloc[-1]), float(line_n.iloc[-11]))
            f.add(f"sma{n}.slope_pct", slope, f"שיפוע ממוצע {n} (אחוז ב-10 ימי מסחר)", "%", 2)
            f.add(f"sma{n}.direction", "עולה" if slope > 0.3 else "יורד" if slope < -0.3 else "שטוח",
                  f"כיוון ממוצע {n}")
    sma50 = stack["values"].get(50)
    if sma50 is not None and math.isfinite(sma50) and math.isfinite(atr_now) and atr_now > 0:
        stretch = (price - sma50) / atr_now
        f.add("sma50.distance_atr", stretch, "המרחק מממוצע 50 ביחידות ATR", "ATR", 1)
        if abs(stretch) >= rules["stretch_atr"]:
            f.add("stretch", f"המחיר מתוח: {abs(stretch):.1f} ATR {'מעל ' if stretch > 0 else 'מתחת ל'}ממוצע 50",
                  "המחיר רחוק מממוצע 50 (תנועה מהירה; מקום פחות נוח להיכנס)")
    known = [v for v in stack["values"].values() if v is not None and math.isfinite(v)]
    if len(known) == len(stack["values"]):
        spread = (max(known) - min(known)) / price * 100
        f.add("ma.spread_pct", spread, "הפער בין הממוצע הגבוה לנמוך", "%", 1)
        if spread <= rules["congestion_ma_spread_pct"]:
            span = bars.iloc[max(0, last - int(rules["range_sessions"]) + 1):]
            f.add("range.high", float(span["high"].max()), "ראש הטווח (החודשים האחרונים)", "$")
            f.add("range.low", float(span["low"].min()), "תחתית הטווח (החודשים האחרונים)", "$")
            f.add("range.sessions", len(span), "אורך הטווח בימי מסחר", "", 0)
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
        crossed = _zone_break(close.to_numpy(float), zone.low, zone.high, last, int(rules["event_fresh_sessions"]))
        if crossed is not None:
            way, j = crossed
            f.add(f"{key}.broken", "נפרץ כלפי מעלה (התנגדות שהפכה לתמיכה)" if way == "up" else
                  "נשבר כלפי מטה (תמיכה שהפכה להתנגדות)", f"המחיר נסגר מעבר ל-{key} לאחרונה")
            f.add(f"{key}.broken_day", _day(bars, j), f"יום הפריצה/השבירה של {key}")
            f.add(f"{key}.broken_sessions_ago", last - j, f"ימי מסחר מאז הפריצה/השבירה של {key}", "", 0)
            f.add(f"{key}.broken_volume_ratio", _volume_ratio(bars, j),
                  f"הנפח ביום הפריצה/השבירה של {key} חלקי ממוצע 50 יום", "x", 2)
    for tl in trendlines(bars, minor, atr, rules):
        key, now_value = tl.id, tl.at(last)
        slope10 = _pct(tl.at(last), tl.at(last - 10)) if last >= 10 else math.nan
        crossed = (tl.kind == "resistance" and now_value < price) or (tl.kind == "support" and now_value > price)
        f.add(f"{key}.kind", KIND_HE[tl.kind] + (" שהמחיר כבר חצה" if crossed else ""), f"סוג קו המגמה {key}")
        f.add(f"{key}.side", "מתחת למחיר" if now_value < price else "מעל המחיר", f"צד קו המגמה {key}")
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
        if (div.kind == "bullish" and price < float(div.price2)) or (div.kind == "bearish" and price > float(div.price2)):
            continue                                    # the price went past it: it failed
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
            # where the price is now against the broken line, extended to today (review
            # round 1: a breakdown the price had closed back above was told as live;
            # round 2: a throwback still under a rising line was told as failed)
            up = det.direction == "bullish"
            word, line_word = ("הפריצה", "קו הפריצה") if up else ("השבירה", "קו השבירה")
            b = int((bars["timestamp"] <= det.breakout_date).sum()) - 1
            since = last - b
            index = {day: i for i, day in enumerate(bars["timestamp"].dt.strftime("%Y-%m-%d"))}
            level = _broken_line_now(det.lines, float(det.breakout_price), index, b, last)
            back = price < level if up else price > level
            after = bars.iloc[b + 1:]
            went = (float(after["high"].max()) - level if up else level - float(after["low"].min())) if len(after) else 0.0
            far_i = int(after["high"].idxmax() if up else after["low"].idxmin()) if len(after) else last
            retest = (not back and math.isfinite(atr_now) and abs(price - level) <= atr_now
                      and went >= rules["retest_away_atr"] * atr_now
                      and last - far_i >= 3)             # it moved away, then came back over days
            f.add(f"{key}.sessions_since_breakout", since, f"ימי מסחר מאז {word} של {key}", "", 0)
            f.add(f"{key}.line_now", level, f"{line_word} של {key}, ממשיך עד היום", "$")
            f.add(f"{key}.close_vs_breakout_pct", _pct(price, level), f"הסגירה ביחס ל{line_word} של {key} היום", "%", 1)
            cancel_at = invalidation(record, bars)
            dead = cancel_at is not None and math.isfinite(float(cancel_at)) and (
                price < float(cancel_at) if up else price > float(cancel_at))
            f.add(f"{key}.state", (f"התבנית נכשלה: המחיר עבר את רמת הביטול שלה ({float(cancel_at):.2f})" if dead else
                                   f"המחיר חזר אל תוך התבנית: {word} מוטלת בספק" if back else
                                   f"המחיר חזר לבדוק את {line_word} מ{'למעלה' if up else 'למטה'}" if retest else
                                   f"המחיר מעל {line_word}" if up else f"המחיר מתחת ל{line_word}"),
                  f"מצב {word} של {key} היום")
            f.add(f"{key}.breakout_volume_ratio", _volume_ratio(bars, b),
                  f"הנפח ביום {word} של {key} חלקי ממוצע 50 יום", "x", 2)
            target = float(det.target) if det.target is not None and math.isfinite(float(det.target)) else None
            if target is not None and len(after):
                hit = after[(after["high"] >= target) if up else (after["low"] <= target)]
                if len(hit):
                    f.add(f"{key}.target_reached_day", _day(bars, int(hit.index[0])),
                          f"היעד של {key} לפי גובה התבנית כבר הושג ביום")
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
