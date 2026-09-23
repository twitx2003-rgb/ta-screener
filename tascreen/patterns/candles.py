"""Candlestick patterns after Bulkowski (Encyclopedia of Candlestick Charts).

Definitions follow thepatternsite.com's identification guidelines, in our own
words (see rules.yaml). Where a guideline is qualitative ("tall", "small",
"little or no shadow", "within pennies") the number is ours and marked so.

Terms, all measured on the candle itself or against the average body of the
`avg_body_sessions` sessions before it:
    body   = |close - open|          range = high - low
    white  = close > open            black = close < open
    tall   = body >= tall_body_factor  x average body
    small  = body <= small_body_factor x average body
    doji   = body <= doji_max_body_frac x range
Prior trend: the close before the pattern against the close `trend_sessions`
earlier (lower -> downtrend, higher -> uptrend).
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from .detection import Check, Detection
from .rules import Rules


class _Candles:
    def __init__(self, bars: pd.DataFrame, rules: Rules):
        self.r = rules
        self.o = bars["open"].to_numpy(float)
        self.h = bars["high"].to_numpy(float)
        self.l = bars["low"].to_numpy(float)
        self.c = bars["close"].to_numpy(float)
        self.body = np.abs(self.c - self.o)
        self.rng = self.h - self.l
        self.upper = self.h - np.maximum(self.o, self.c)
        self.lower = np.minimum(self.o, self.c) - self.l
        n = int(rules.cg("avg_body_sessions"))
        self.avg_body = pd.Series(self.body).rolling(n, min_periods=n).mean().shift(1).to_numpy()

    # -- terms ------------------------------------------------------------
    def white(self, i): return self.c[i] > self.o[i]
    def black(self, i): return self.c[i] < self.o[i]

    def body_ratio(self, i) -> float:
        a = self.avg_body[i]
        return self.body[i] / a if a and not math.isnan(a) else math.nan

    def trend(self, start: int) -> float:
        """% change of the close before `start` over `trend_sessions` sessions."""
        n = int(self.r.cg("trend_sessions"))
        a, b = start - 1 - n, start - 1
        if a < 0:
            return math.nan
        return (self.c[b] / self.c[a] - 1) * 100


class _Rules:
    """Collects the checks of one candidate; stops at the first failure."""

    def __init__(self, cs: _Candles):
        self.cs, self.checks = cs, []

    def need(self, rule: str, label: str, value, threshold, ok: bool, param: str = "") -> bool:
        origin = self.cs.r.candle_general[param].origin if param else ""
        self.checks.append(Check(rule, label, value, threshold, bool(ok), origin))
        return bool(ok)

    # shared rules
    def downtrend(self, start):
        t = self.cs.trend(start)
        return self.need("prior_trend", "מגמה יורדת לפני התבנית", t, "< 0",
                         not math.isnan(t) and t < 0, "trend_sessions")

    def uptrend(self, start):
        t = self.cs.trend(start)
        return self.need("prior_trend", "מגמה עולה לפני התבנית", t, "> 0",
                         not math.isnan(t) and t > 0, "trend_sessions")

    def tall(self, i, label="נר גבוה"):
        f = self.cs.r.cg("tall_body_factor")
        ratio = self.cs.body_ratio(i)
        return self.need("tall", label, ratio, f">= {f}", not math.isnan(ratio) and ratio >= f,
                         "tall_body_factor")

    def small(self, i, label="גוף קטן"):
        f = self.cs.r.cg("small_body_factor")
        ratio = self.cs.body_ratio(i)
        return self.need("small", label, ratio, f"<= {f}", not math.isnan(ratio) and ratio <= f,
                         "small_body_factor")

    def not_doji(self, i):
        f = self.cs.r.cg("doji_max_body_frac")
        frac = self.cs.body[i] / self.cs.rng[i] if self.cs.rng[i] else 0.0
        return self.need("not_doji", "לא דוג'י", frac, f"> {f}", frac > f, "doji_max_body_frac")

    def doji(self, i):
        f = self.cs.r.cg("doji_max_body_frac")
        frac = self.cs.body[i] / self.cs.rng[i] if self.cs.rng[i] else math.nan
        return self.need("doji", "פתיחה וסגירה כמעט באותו מחיר", frac, f"<= {f}",
                         not math.isnan(frac) and frac <= f, "doji_max_body_frac")

    def long_shadow(self, i, which: str):
        m = self.cs.r.cg("long_shadow_body_multiple")
        shadow = self.cs.lower[i] if which == "lower" else self.cs.upper[i]
        ratio = shadow / self.cs.body[i] if self.cs.body[i] else math.inf
        label = "צל תחתון ארוך" if which == "lower" else "צל עליון ארוך"
        return self.need(f"long_{which}_shadow", label, ratio, f">= {m}x body", ratio >= m,
                         "long_shadow_body_multiple")

    def little_shadow(self, i, which: str):
        f = self.cs.r.cg("little_shadow_max_frac")
        shadow = self.cs.lower[i] if which == "lower" else self.cs.upper[i]
        frac = shadow / self.cs.rng[i] if self.cs.rng[i] else 0.0
        label = "כמעט ללא צל תחתון" if which == "lower" else "כמעט ללא צל עליון"
        return self.need(f"little_{which}_shadow", label, frac, f"<= {f}", frac <= f,
                         "little_shadow_max_frac")

    def close_near(self, i, extreme: str):
        f = self.cs.r.cg("near_extreme_frac")
        cs = self.cs
        dist = (cs.h[i] - cs.c[i]) if extreme == "high" else (cs.c[i] - cs.l[i])
        frac = dist / cs.rng[i] if cs.rng[i] else 0.0
        label = "סגירה ליד השיא" if extreme == "high" else "סגירה ליד השפל"
        return self.need(f"close_near_{extreme}", label, frac, f"<= {f}", frac <= f,
                         "near_extreme_frac")

    def color(self, i, want: str):
        ok = self.cs.white(i) if want == "white" else self.cs.black(i)
        return self.need(f"{want}_candle", "נר לבן" if want == "white" else "נר שחור",
                         "white" if self.cs.white(i) else "black" if self.cs.black(i) else "flat",
                         want, ok)


# Each detector gets the index of the pattern's last candle and returns its
# checks when every rule passes, else None.
def _hammer(cs, t):
    r = _Rules(cs)
    ok = (r.downtrend(t) and r.not_doji(t) and r.long_shadow(t, "lower")
          and r.little_shadow(t, "upper"))
    return (t, r.checks) if ok else None


def _hanging_man(cs, t):
    r = _Rules(cs)
    ok = (r.uptrend(t) and r.not_doji(t) and r.long_shadow(t, "lower")
          and r.little_shadow(t, "upper"))
    return (t, r.checks) if ok else None


def _inverted_hammer(cs, t):
    a = t - 1
    r = _Rules(cs)
    ok = (a >= 0 and r.downtrend(a) and r.color(a, "black") and r.tall(a, "נר שחור גבוה")
          and r.close_near(a, "low") and r.small(t, "נר שני קצר") and r.not_doji(t)
          and r.need("opens_below_prior_close", "נפתח מתחת לסגירה הקודמת", cs.o[t], f"< {cs.c[a]:.4g}",
                     cs.o[t] < cs.c[a])
          and r.long_shadow(t, "upper") and r.little_shadow(t, "lower"))
    return (a, r.checks) if ok else None


def _shooting_star(cs, t):
    r = _Rules(cs)
    ok = (r.uptrend(t) and r.small(t) and r.not_doji(t) and r.long_shadow(t, "upper")
          and r.little_shadow(t, "lower"))
    return (t, r.checks) if ok else None


def _engulfing(cs, t, bullish: bool):
    a = t - 1
    r = _Rules(cs)
    first, second = ("black", "white") if bullish else ("white", "black")
    trend = r.downtrend if bullish else r.uptrend
    ok = (a >= 0 and trend(a) and r.color(a, first) and r.color(t, second)
          and r.need("taller", "הנר השני גבוה מהראשון", cs.body[t] / cs.body[a] if cs.body[a] else math.inf,
                     "> 1", cs.body[t] > cs.body[a]))
    if not ok:
        return None
    if bullish:
        ok = r.need("engulfs", "גוף הלבן בולע את גוף השחור", f"{cs.o[t]:.4g}..{cs.c[t]:.4g}",
                    f"open < {cs.c[a]:.4g}, close > {cs.o[a]:.4g}", cs.o[t] < cs.c[a] and cs.c[t] > cs.o[a])
    else:
        ok = r.need("engulfs", "גוף השחור בולע את גוף הלבן", f"{cs.o[t]:.4g}..{cs.c[t]:.4g}",
                    f"open > {cs.c[a]:.4g}, close < {cs.o[a]:.4g}", cs.o[t] > cs.c[a] and cs.c[t] < cs.o[a])
    return (a, r.checks) if ok else None


def _piercing(cs, t):
    a = t - 1
    r = _Rules(cs)
    mid = (cs.o[a] + cs.c[a]) / 2 if a >= 0 else math.nan
    ok = (a >= 0 and r.downtrend(a) and r.color(a, "black") and r.color(t, "white")
          and r.need("opens_below_low", "נפתח מתחת לשפל של השחור", cs.o[t], f"< {cs.l[a]:.4g}",
                     cs.o[t] < cs.l[a])
          and r.need("closes_into_body", "נסגר בין אמצע גוף השחור לפתיחתו", cs.c[t],
                     f"{mid:.4g} < close < {cs.o[a]:.4g}", mid < cs.c[t] < cs.o[a]))
    return (a, r.checks) if ok else None


def _dark_cloud(cs, t):
    a = t - 1
    r = _Rules(cs)
    mid = (cs.o[a] + cs.c[a]) / 2 if a >= 0 else math.nan
    ok = (a >= 0 and r.uptrend(a) and r.color(a, "white") and r.tall(a, "נר לבן גבוה")
          and r.color(t, "black")
          and r.need("opens_above_high", "נפתח מעל השיא של הלבן", cs.o[t], f"> {cs.h[a]:.4g}",
                     cs.o[t] > cs.h[a])
          and r.need("closes_below_mid", "נסגר מתחת לאמצע גוף הלבן", cs.c[t], f"< {mid:.4g}",
                     cs.c[t] < mid))
    return (a, r.checks) if ok else None


def _star(cs, t, morning: bool):
    a, b = t - 2, t - 1
    r = _Rules(cs)
    if a < 0:
        return None
    top = lambda i: max(cs.o[i], cs.c[i])       # noqa: E731
    bottom = lambda i: min(cs.o[i], cs.c[i])    # noqa: E731
    mid = (cs.o[a] + cs.c[a]) / 2
    if morning:
        ok = (r.downtrend(a) and r.color(a, "black") and r.tall(a, "נר שחור גבוה")
              and r.small(b, "נר אמצעי עם גוף קטן")
              and r.need("gap_down", "הגוף הקטן בפער מתחת לגוף הראשון", top(b), f"< {bottom(a):.4g}",
                         top(b) < bottom(a))
              and r.color(t, "white") and r.tall(t, "נר לבן גבוה")
              and r.need("gap_up", "הנר השלישי בפער מעל הגוף הקטן", bottom(t), f"> {top(b):.4g}",
                         bottom(t) > top(b))
              and r.need("closes_midway", "נסגר לפחות באמצע גוף הנר הראשון", cs.c[t], f">= {mid:.4g}",
                         cs.c[t] >= mid))
    else:
        ok = (r.uptrend(a) and r.color(a, "white") and r.tall(a, "נר לבן גבוה")
              and r.small(b, "נר אמצעי עם גוף קטן")
              and r.need("gap_up", "הגוף הקטן בפער מעל שני הגופים הסמוכים", bottom(b),
                         f"> {max(top(a), top(t)):.4g}", bottom(b) > max(top(a), top(t)))
              and r.color(t, "black") and r.tall(t, "נר שחור גבוה")
              and r.need("opens_below", "נפתח מתחת לגוף הקטן", cs.o[t], f"< {bottom(b):.4g}",
                         cs.o[t] < bottom(b))
              and r.need("closes_midway", "נסגר לפחות באמצע גוף הנר הראשון", cs.c[t], f"<= {mid:.4g}",
                         cs.c[t] <= mid))
    return (a, r.checks) if ok else None


def _harami(cs, t, bullish: bool):
    a = t - 1
    r = _Rules(cs)
    if a < 0:
        return None
    first, second = ("black", "white") if bullish else ("white", "black")
    trend = r.downtrend if bullish else r.uptrend
    lo_a, hi_a = min(cs.o[a], cs.c[a]), max(cs.o[a], cs.c[a])
    lo_t, hi_t = min(cs.o[t], cs.c[t]), max(cs.o[t], cs.c[t])
    inside = lo_t >= lo_a and hi_t <= hi_a and not (lo_t == lo_a and hi_t == hi_a)
    ok = (trend(a) and r.color(a, first) and r.tall(a, f"נר {'שחור' if bullish else 'לבן'} גבוה")
          and r.color(t, second) and (bullish or r.small(t, "נר שחור קטן"))
          and r.need("inside_body", "הגוף השני בתוך גוף הראשון", f"{lo_t:.4g}..{hi_t:.4g}",
                     f"{lo_a:.4g}..{hi_a:.4g}", inside))
    return (a, r.checks) if ok else None


def _three(cs, t, white: bool):
    a = t - 2
    r = _Rules(cs)
    if a < 0:
        return None
    color = "white" if white else "black"
    ok = r.downtrend(a) if white else r.uptrend(a)
    for i in (a, a + 1, t):
        ok = ok and r.color(i, color) and r.tall(i) and r.close_near(i, "high" if white else "low")
    for i in (a + 1, t):
        lo, hi = min(cs.o[i - 1], cs.c[i - 1]), max(cs.o[i - 1], cs.c[i - 1])
        ok = ok and r.need(f"opens_in_body_{i - a}", "נפתח בתוך גוף הנר הקודם", cs.o[i],
                           f"{lo:.4g}..{hi:.4g}", lo <= cs.o[i] <= hi)
        if white:
            ok = ok and r.need(f"higher_close_{i - a}", "סגירה גבוהה יותר", cs.c[i],
                               f"> {cs.c[i - 1]:.4g}", cs.c[i] > cs.c[i - 1])
        else:
            ok = ok and r.need(f"new_low_{i - a}", "סגירה ושפל חדשים נמוכים יותר", cs.c[i],
                               f"< {cs.c[i - 1]:.4g}", cs.c[i] < cs.c[i - 1] and cs.l[i] < cs.l[i - 1])
    return (a, r.checks) if ok else None


def _doji(cs, t, northern: bool):
    r = _Rules(cs)
    ok = (r.uptrend(t) if northern else r.downtrend(t)) and r.doji(t)
    return (t, r.checks) if ok else None


def _marubozu(cs, t, white: bool):
    r = _Rules(cs)
    f = cs.r.cg("marubozu_max_shadow_frac")
    shadows = (cs.upper[t] + cs.lower[t]) / cs.rng[t] if cs.rng[t] else math.nan
    ok = (r.color(t, "white" if white else "black") and r.tall(t)
          and r.need("no_shadows", "ללא צללים", shadows, f"<= {f}",
                     not math.isnan(shadows) and shadows <= f, "marubozu_max_shadow_frac"))
    return (t, r.checks) if ok else None


DETECTORS: dict[str, Callable] = {
    "hammer": _hammer,
    "hanging_man": _hanging_man,
    "inverted_hammer": _inverted_hammer,
    "shooting_star": _shooting_star,
    "bullish_engulfing": lambda cs, t: _engulfing(cs, t, True),
    "bearish_engulfing": lambda cs, t: _engulfing(cs, t, False),
    "piercing": _piercing,
    "dark_cloud_cover": _dark_cloud,
    "morning_star": lambda cs, t: _star(cs, t, True),
    "evening_star": lambda cs, t: _star(cs, t, False),
    "bullish_harami": lambda cs, t: _harami(cs, t, True),
    "bearish_harami": lambda cs, t: _harami(cs, t, False),
    "three_white_soldiers": lambda cs, t: _three(cs, t, True),
    "three_black_crows": lambda cs, t: _three(cs, t, False),
    "northern_doji": lambda cs, t: _doji(cs, t, True),
    "southern_doji": lambda cs, t: _doji(cs, t, False),
    "white_marubozu": lambda cs, t: _marubozu(cs, t, True),
    "black_marubozu": lambda cs, t: _marubozu(cs, t, False),
}


def detect_candles(bars: pd.DataFrame, symbol: str, rules: Rules,
                   recent: int | None = None) -> list[Detection]:
    """Candle patterns whose last candle is among the last `recent` sessions."""
    missing = set(rules.candle) ^ set(DETECTORS)
    if missing:
        raise ValueError(f"candle detectors and rules.yaml disagree on {sorted(missing)}")
    if len(bars) < int(rules.cg("avg_body_sessions")) + 3:
        return []
    cs = _Candles(bars, rules)
    recent = recent or int(rules.cg("recent_sessions"))
    dates = bars["timestamp"]
    out = []
    for t in range(len(bars) - recent, len(bars)):
        for key, detector in DETECTORS.items():
            found = detector(cs, t)
            if found is None:
                continue
            start, checks = found
            spec = rules.candle[key]
            out.append(Detection(
                symbol=symbol, family="candle", pattern=key, direction=spec.direction,
                status="signal", start=dates.iloc[start], end=dates.iloc[t], checks=checks,
                points=[{"date": dates.iloc[i].date().isoformat(), "price": float(cs.h[i]),
                         "label": ""} for i in range(start, t + 1)]))
    return out
