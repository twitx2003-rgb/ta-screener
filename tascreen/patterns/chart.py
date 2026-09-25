"""Chart patterns after Bulkowski (Encyclopedia of Chart Patterns).

Built on ATR-scaled zigzag pivots (pivots.py). Every threshold comes from
rules.yaml; each detection carries the checklist of rules it passed.

Status:
- forming   the pattern is complete up to its last turning point but has not
            broken out; reported while that turning point is recent
            (`forming_max_age_sessions`) and price has not invalidated it;
- breakout  price closed beyond the confirmation level/trendline, within
            `recent_breakout_sessions`;
- busted    it broke out, then closed back beyond the pattern's extreme.

Targets use the classic full-height measure rule (Bulkowski scales the height
by a success percentage from his statistics, which are not reproduced here).
A target is the book's rule of thumb, not a forecast.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from ..indicators import atr as atr_series
from .detection import Check, Detection, pct_diff, volume_trend
from .pivots import Pivot, zigzag
from .rules import PatternRules, Rules

TRENDLINE_SEARCH_PIVOTS = (9, 8, 7, 6, 5)       # longest first
TRENDLINE_RECENT_PIVOT_BARS = 150               # only fit patterns ending this recently


class Series:
    """One symbol's bars as arrays, with ATR and pivots computed once."""

    def __init__(self, bars: pd.DataFrame, rules: Rules, symbol: str):
        self.symbol = symbol
        self.rules = rules
        self.dates = bars["timestamp"].reset_index(drop=True)
        self.o = bars["open"].to_numpy(float)
        self.h = bars["high"].to_numpy(float)
        self.l = bars["low"].to_numpy(float)
        self.c = bars["close"].to_numpy(float)
        self.v = bars["volume"].reset_index(drop=True)
        self.atr = atr_series(bars, int(rules.g("atr_period"))).to_numpy()
        self.pivots = zigzag(self.h, self.l, self.atr, rules.g("pivot_atr_multiple"))
        self.last = len(bars) - 1

    def date(self, i: int) -> str:
        return self.dates.iloc[i].date().isoformat()

    def first_close(self, start: int, stop: int, beyond: Callable[[int], bool]) -> int | None:
        for i in range(max(start, 0), min(stop, self.last) + 1):
            if beyond(i):
                return i
        return None

    def trend_change(self, i: int, price: float) -> float:
        """% change from the close `trend_lookback_sessions` before bar i to `price`."""
        j = i - int(self.rules.g("trend_lookback_sessions"))
        return (price / self.c[j] - 1) * 100 if j >= 0 else math.nan


class Checks:
    def __init__(self, spec: PatternRules | None = None):
        self.spec, self.items = spec, []

    def need(self, rule: str, label: str, value, threshold, ok: bool, param: str = "") -> bool:
        origin = ""
        if param and self.spec is not None and param in self.spec.params:
            origin = self.spec.params[param].origin
        elif param == "trend":
            origin = "site"
        self.items.append(Check(rule, label, value, threshold, bool(ok), origin))
        return bool(ok)


def _status(s: Series, end_i: int, breakout_i: int | None, busted_i: int | None) -> str | None:
    """forming / breakout / busted, or None when the pattern is too old to report."""
    r = s.rules
    if breakout_i is None:
        return "forming" if s.last - end_i <= r.g("forming_max_age_sessions") else None
    if s.last - breakout_i > r.g("recent_breakout_sessions"):
        return None
    return "busted" if busted_i is not None else "breakout"


def _new(s: Series, spec: PatternRules, direction: str, status: str, start_i: int, end_i: int,
         checks: Checks, pivots: list[Pivot], labels: list[str], breakout_i: int | None,
         breakout_price: float, height: float, target: float, lines: list[dict],
         triggers: tuple[float, float] = (math.nan, math.nan)) -> Detection:
    """`triggers` = (up, down): the levels a close must cross in the session after the
    last bar; kept only while the pattern is still forming."""
    up, down = triggers if status == "forming" else (math.nan, math.nan)
    return Detection(
        symbol=s.symbol, family="chart", pattern=spec.key, direction=direction, status=status,
        start=s.dates.iloc[start_i], end=s.dates.iloc[end_i],
        breakout_date=s.dates.iloc[breakout_i] if breakout_i is not None else None,
        breakout_price=breakout_price, height=height, target=target,
        volume_trend=volume_trend(s.v.iloc[start_i:end_i + 1]),
        points=[{"date": s.date(p.i), "price": p.price, "label": lab} for p, lab in zip(pivots, labels)],
        lines=lines, checks=checks.items,
        trigger_up=float(up), trigger_down=float(down))


def _line(s: Series, i1: int, y1: float, i2: int, y2: float, label: str) -> dict:
    return {"x1": s.date(i1), "y1": round(float(y1), 4), "x2": s.date(i2), "y2": round(float(y2), 4),
            "label": label}


# --------------------------------------------------------------- double tops/bottoms
def _double(s: Series, bottom: bool) -> list[tuple[Detection, frozenset]]:
    key = "double_bottom" if bottom else "double_top"
    spec = s.rules.chart[key]
    kinds = ("L", "H", "L") if bottom else ("H", "L", "H")
    diff_p = "max_bottom_diff_pct" if bottom else "max_top_diff_pct"
    swing_p = "min_rise_between_pct" if bottom else "min_decline_between_pct"
    out = []
    P = s.pivots
    for j in range(len(P) - 2):
        a, m, b = P[j], P[j + 1], P[j + 2]
        if (a.kind, m.kind, b.kind) != kinds or a.i < s.last - s.rules.g("lookback_sessions"):
            continue
        ck = Checks(spec)
        extreme = min(a.price, b.price) if bottom else max(a.price, b.price)
        swing = (m.price / extreme - 1) * 100 if bottom else (1 - m.price / extreme) * 100
        sep = b.i - a.i
        trend = s.trend_change(a.i, a.price)
        ok = (ck.need("prior_trend", "מגמה יורדת לפני התבנית" if bottom else "מגמה עולה לפני התבנית",
                      trend, "< 0" if bottom else "> 0",
                      not math.isnan(trend) and (trend < 0 if bottom else trend > 0), "trend")
              and ck.need(diff_p, "הפרש בין השפלים (%)" if bottom else "הפרש בין הפסגות (%)",
                          pct_diff(a.price, b.price), f"<= {spec.p(diff_p)}",
                          pct_diff(a.price, b.price) <= spec.p(diff_p), diff_p)
              and ck.need(swing_p, "עלייה בין השפלים (%)" if bottom else "ירידה בין הפסגות (%)",
                          swing, f">= {spec.p(swing_p)}", swing >= spec.p(swing_p), swing_p)
              and ck.need("separation", "מרחק בין השפלים (ימי מסחר)" if bottom else "מרחק בין הפסגות (ימי מסחר)",
                          sep, f"{spec.p('min_separation_sessions'):g}-{spec.p('max_separation_sessions'):g}",
                          spec.p("min_separation_sessions") <= sep <= spec.p("max_separation_sessions"),
                          "min_separation_sessions"))
        if not ok:
            continue
        confirm = (lambda i: s.c[i] > m.price) if bottom else (lambda i: s.c[i] < m.price)
        failed = (lambda i: s.c[i] < extreme) if bottom else (lambda i: s.c[i] > extreme)
        brk = s.first_close(b.i + 1, s.last, confirm)
        early_fail = s.first_close(b.i + 1, (brk - 1) if brk is not None else s.last, failed)
        if early_fail is not None:
            continue                            # broke the pattern's extreme before confirming
        busted = s.first_close(brk + 1, s.last, failed) if brk is not None else None
        status = _status(s, b.i, brk, busted)
        if status is None:
            continue
        height = abs(m.price - extreme)
        ck.need("confirmation", "אישור: סגירה מעל השיא שבין השפלים" if bottom
                else "אישור: סגירה מתחת לשפל שבין הפסגות", s.c[brk] if brk is not None else None,
                f"{'>' if bottom else '<'} {m.price:.4g}", brk is not None)
        end_line = brk if brk is not None else s.last
        det = _new(s, spec, spec.direction, status, a.i, b.i, ck, [a, m, b],
                   ["שפל 1", "שיא ביניים", "שפל 2"] if bottom else ["פסגה 1", "שפל ביניים", "פסגה 2"],
                   brk, m.price, height, m.price + height if bottom else m.price - height,
                   [_line(s, m.i, m.price, end_line, m.price, "קו אישור")],
                   (m.price, math.nan) if bottom else (math.nan, m.price))
        out.append((det, frozenset((a.i, m.i, b.i))))
    return out


# ------------------------------------------------------------------- triple
def _triple(s: Series, bottom: bool) -> list[tuple[Detection, frozenset]]:
    key = "triple_bottom" if bottom else "triple_top"
    spec = s.rules.chart[key]
    kinds = ("L", "H", "L", "H", "L") if bottom else ("H", "L", "H", "L", "H")
    diff_p = "max_bottom_diff_pct" if bottom else "max_top_diff_pct"
    out = []
    P = s.pivots
    for j in range(len(P) - 4):
        seq = P[j:j + 5]
        if tuple(p.kind for p in seq) != kinds or seq[0].i < s.last - s.rules.g("lookback_sessions"):
            continue
        prices = [seq[k].price for k in (0, 2, 4)]
        extreme = min(prices) if bottom else max(prices)
        spread = (max(prices) / min(prices) - 1) * 100
        # every swing between a middle turning point and its two neighbours
        swings = [abs(seq[m].price / seq[m + d].price - 1) * 100 for m in (1, 3) for d in (-1, 1)]
        level = max(seq[1].price, seq[3].price) if bottom else min(seq[1].price, seq[3].price)
        trend = s.trend_change(seq[0].i, seq[0].price)
        ck = Checks(spec)
        ok = (ck.need("prior_trend", "מגמה יורדת לפני התבנית" if bottom else "מגמה עולה לפני התבנית",
                      trend, "< 0" if bottom else "> 0",
                      not math.isnan(trend) and (trend < 0 if bottom else trend > 0), "trend")
              and ck.need(diff_p, "פיזור מחירי השפלים (%)" if bottom else "פיזור מחירי הפסגות (%)",
                          spread, f"<= {spec.p(diff_p)}", spread <= spec.p(diff_p), diff_p)
              and ck.need("min_swing_pct", "תנודה מינימלית בין השפלים (%)" if bottom
                          else "תנודה מינימלית בין הפסגות (%)", min(swings),
                          f">= {spec.p('min_swing_pct')}", min(swings) >= spec.p("min_swing_pct"),
                          "min_swing_pct"))
        if not ok:
            continue
        confirm = (lambda i: s.c[i] > level) if bottom else (lambda i: s.c[i] < level)
        failed = (lambda i: s.c[i] < extreme) if bottom else (lambda i: s.c[i] > extreme)
        brk = s.first_close(seq[4].i + 1, s.last, confirm)
        if s.first_close(seq[4].i + 1, (brk - 1) if brk is not None else s.last, failed) is not None:
            continue
        busted = s.first_close(brk + 1, s.last, failed) if brk is not None else None
        status = _status(s, seq[4].i, brk, busted)
        if status is None:
            continue
        height = abs(level - extreme)
        ck.need("confirmation", "אישור: סגירה מעל השיא הגבוה שבין השפלים" if bottom
                else "אישור: סגירה מתחת לשפל הנמוך שבין הפסגות",
                s.c[brk] if brk is not None else None, f"{'>' if bottom else '<'} {level:.4g}",
                brk is not None)
        labels = ["שפל 1", "שיא", "שפל 2", "שיא", "שפל 3"] if bottom else \
                 ["פסגה 1", "שפל", "פסגה 2", "שפל", "פסגה 3"]
        det = _new(s, spec, spec.direction, status, seq[0].i, seq[4].i, ck, seq, labels, brk, level,
                   height, level + height if bottom else level - height,
                   [_line(s, seq[1].i, level, brk if brk is not None else s.last, level, "קו אישור")],
                   (level, math.nan) if bottom else (math.nan, level))
        out.append((det, frozenset(p.i for p in seq)))
    return out


# ------------------------------------------------------------ head and shoulders
def _head_shoulders(s: Series, top: bool) -> list[tuple[Detection, frozenset]]:
    key = "head_shoulders_top" if top else "head_shoulders_bottom"
    spec = s.rules.chart[key]
    kinds = ("H", "L", "H", "L", "H") if top else ("L", "H", "L", "H", "L")
    out = []
    P = s.pivots
    for j in range(len(P) - 4):
        ls, a1, head, a2, rs = P[j:j + 5]
        if (ls.kind, a1.kind, head.kind, a2.kind, rs.kind) != kinds:
            continue
        if ls.i < s.last - s.rules.g("lookback_sessions"):
            continue
        outer = max(ls.price, rs.price) if top else min(ls.price, rs.price)
        excess = (head.price / outer - 1) * 100 if top else (1 - head.price / outer) * 100
        d1, d2 = head.i - ls.i, rs.i - head.i
        asym = max(d1, d2) / min(d1, d2)
        trend = s.trend_change(ls.i, ls.price)
        ck = Checks(spec)
        ok = (ck.need("prior_trend", "מגמה עולה לפני התבנית" if top else "מגמה יורדת לפני התבנית",
                      trend, "> 0" if top else "< 0",
                      not math.isnan(trend) and (trend > 0 if top else trend < 0), "trend")
              and ck.need("min_head_excess_pct", "הראש בולט מעבר לכתפיים (%)", excess,
                          f">= {spec.p('min_head_excess_pct')}", excess >= spec.p("min_head_excess_pct"),
                          "min_head_excess_pct")
              and ck.need("max_shoulder_diff_pct", "הפרש בין הכתפיים (%)", pct_diff(ls.price, rs.price),
                          f"<= {spec.p('max_shoulder_diff_pct')}",
                          pct_diff(ls.price, rs.price) <= spec.p("max_shoulder_diff_pct"),
                          "max_shoulder_diff_pct")
              and ck.need("max_time_asymmetry", "סימטריה בזמן (יחס המרחקים מהראש)", asym,
                          f"<= {spec.p('max_time_asymmetry')}", asym <= spec.p("max_time_asymmetry"),
                          "max_time_asymmetry"))
        if not ok:
            continue
        slope = (a2.price - a1.price) / (a2.i - a1.i)

        def neck(i: int) -> float:
            return a1.price + slope * (i - a1.i)

        # the neckline runs between the head and shoulders and the armpits: a steep one that
        # had passed a shoulder "confirmed" a bottom below both shoulders (review round 2)
        sides = all((neck(p.i) < p.price) if top else (neck(p.i) > p.price) for p in (ls, head, rs))
        if not ck.need("neckline_side", "קו הצוואר בצד הנכון של הראש והכתפיים", sides, True, sides):
            continue

        # Bulkowski's confirmation: through the neckline when it slopes the helpful
        # way, otherwise through the right armpit; a line extended past the head's price
        # confirms nothing.
        if top:
            level = neck if slope > 0 else (lambda i: a2.price)
            confirm = lambda i: level(i) < head.price and s.c[i] < level(i)     # noqa: E731
            failed = lambda i: s.c[i] > head.price                 # noqa: E731
        else:
            level = neck if slope < 0 else (lambda i: a2.price)
            confirm = lambda i: level(i) > head.price and s.c[i] > level(i)     # noqa: E731
            failed = lambda i: s.c[i] < head.price                 # noqa: E731
        brk = s.first_close(rs.i + 1, s.last, confirm)
        if s.first_close(rs.i + 1, (brk - 1) if brk is not None else s.last, failed) is not None:
            continue
        busted = s.first_close(brk + 1, s.last, failed) if brk is not None else None
        status = _status(s, rs.i, brk, busted)
        if status is None:
            continue
        height = abs(head.price - neck(head.i))
        brk_price = level(brk) if brk is not None else level(s.last)
        ck.need("confirmation", "אישור: סגירה מעבר לקו הצוואר (או לבית השחי הימני)",
                s.c[brk] if brk is not None else None, f"{'<' if top else '>'} {brk_price:.4g}",
                brk is not None)
        stop = brk if brk is not None else s.last
        labels = ["כתף שמאל", "בית שחי", "ראש", "בית שחי", "כתף ימין"]
        det = _new(s, spec, spec.direction, status, ls.i, rs.i, ck, [ls, a1, head, a2, rs], labels,
                   brk, brk_price, height, brk_price - height if top else brk_price + height,
                   [_line(s, a1.i, a1.price, stop, neck(stop), "קו צוואר")],
                   (math.nan, level(s.last + 1)) if top else (level(s.last + 1), math.nan))
        out.append((det, frozenset(p.i for p in (ls, a1, head, a2, rs))))
    return out


# ------------------------------------------------ triangles, rectangles, wedges
def _fit(points: list[Pivot]) -> tuple[float, float]:
    """Least-squares line y = a + b*i through the points."""
    x = np.array([p.i for p in points], float)
    y = np.array([p.price for p in points], float)
    if len(points) == 2:
        b = (y[1] - y[0]) / (x[1] - x[0])
        return y[0] - b * x[0], b
    b, a = np.polyfit(x, y, 1)
    return a, b


def _classify(top_flat, bot_flat, b_top, b_bot) -> str | None:
    if top_flat and bot_flat:
        return "rectangle"
    if top_flat and b_bot > 0:
        return "ascending_triangle"
    if bot_flat and b_top < 0:
        return "descending_triangle"
    if top_flat or bot_flat:
        return None                           # a flat line with the other diverging
    if b_top < 0 < b_bot:
        return "symmetrical_triangle"
    if b_top > 0 and b_bot > 0 and b_bot > b_top:
        return "rising_wedge"
    if b_top < 0 and b_bot < 0 and b_top < b_bot:
        return "falling_wedge"
    return None                               # channels, broadening shapes: not in v1


def _trendline_candidate(s: Series, seq: list[Pivot]) -> tuple[Detection, frozenset] | None:
    r = s.rules
    highs = [p for p in seq if p.kind == "H"]
    lows = [p for p in seq if p.kind == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return None
    i0, i1 = seq[0].i, seq[-1].i
    a_t, b_t = _fit(highs)
    a_b, b_b = _fit(lows)
    top = lambda i: a_t + b_t * i            # noqa: E731
    bot = lambda i: a_b + b_b * i            # noqa: E731
    if top(i0) <= bot(i0) or top(i1) <= bot(i1):
        return None
    height = float(s.h[i0:i1 + 1].max() - s.l[i0:i1 + 1].min())
    tol = min(r.g("touch_tolerance_atr") * float(np.nanmedian(s.atr[i0:i1 + 1])),
              r.g("touch_tolerance_height") * height)
    if not height or math.isnan(tol):
        return None
    drift = r.g("flat_line_max_drift")
    top_flat = abs(top(i1) - top(i0)) <= drift * height
    bot_flat = abs(bot(i1) - bot(i0)) <= drift * height
    key = _classify(top_flat, bot_flat, b_t, b_b)
    if key is None:
        return None
    spec = r.chart[key]
    ck = Checks(spec)
    top_touch = sum(abs(p.price - top(p.i)) <= tol for p in highs)
    bot_touch = sum(abs(p.price - bot(p.i)) <= tol for p in lows)
    major, minor = max(top_touch, bot_touch), min(top_touch, bot_touch)
    closes = s.c[i0:i1 + 1]
    idx = np.arange(i0, i1 + 1)
    slack = r.g("inside_tolerance_atr") * float(np.nanmedian(s.atr[i0:i1 + 1]))
    inside = bool(np.all((closes <= top(idx) + slack) & (closes >= bot(idx) - slack)))
    ok = (ck.need("touches_all_on_lines", "כל נקודות המפנה על הקווים", f"{top_touch}/{len(highs)}, {bot_touch}/{len(lows)}",
                  "all", top_touch == len(highs) and bot_touch == len(lows))
          and ck.need("min_touches_major", "מגעים בקו אחד", major, f">= {spec.p('min_touches_major'):g}",
                      major >= spec.p("min_touches_major"), "min_touches_major")
          and ck.need("min_touches_minor", "מגעים בקו השני", minor, f">= {spec.p('min_touches_minor'):g}",
                      minor >= spec.p("min_touches_minor"), "min_touches_minor")
          and ck.need("inside_lines", "המחיר נע בין הקווים", inside, True, inside)
          and ck.need("min_duration_sessions", "משך (ימי מסחר)", i1 - i0,
                      f">= {spec.p('min_duration_sessions'):g}", i1 - i0 >= spec.p("min_duration_sessions"),
                      "min_duration_sessions"))
    if not ok:
        return None
    apex = None
    if key != "rectangle":
        apex = (a_b - a_t) / (b_t - b_b)
        share = (top(i1) - bot(i1)) / (top(i0) - bot(i0))
        limit = r.g("converge_max_end_width")
        if not (ck.need("converging", "הקווים מתכנסים (רוחב בסוף/בהתחלה)", share, f"<= {limit}",
                        share <= limit)
                and ck.need("apex_ahead", "הקודקוד עוד לפנינו", round(apex - i1, 1), "> 0",
                            apex > i1)):
            return None
    up = lambda i: s.c[i] > top(i)           # noqa: E731
    down = lambda i: s.c[i] < bot(i)         # noqa: E731
    stop = s.last if apex is None else min(s.last, int(math.floor(apex)))
    brk = s.first_close(i1 + 1, stop, lambda i: up(i) or down(i))
    if brk is None and apex is not None and s.last > apex:
        return None                          # ran into the apex without breaking out
    direction = "either" if brk is None else ("bullish" if up(brk) else "bearish")
    status = _status(s, i1, brk, None)
    if status is None:
        return None
    ck.need("breakout", "פריצה: סגירה מחוץ לקו", s.c[brk] if brk is not None else None,
            "outside a line", brk is not None)
    if key == "rectangle":
        trend = s.trend_change(i0, s.c[i0])
        ck.need("entry_trend", "מגמה לפני התבנית (מלבן תחתית/פסגה)", trend, "info", True, "trend")
    if brk is None:
        breakout_price, target = math.nan, math.nan
    else:
        breakout_price = top(brk) if direction == "bullish" else bot(brk)
        width = top(brk) - bot(brk) if key == "rectangle" else height
        target = breakout_price + width if direction == "bullish" else breakout_price - width
        # Bulkowski's own wedge rule: the opposite end of the wedge is the target
        if key == "falling_wedge" and direction == "bullish":
            target = float(s.h[i0:i1 + 1].max())
        if key == "rising_wedge" and direction == "bearish":
            target = float(s.l[i0:i1 + 1].min())
    end_x = brk if brk is not None else s.last
    labels = ["" for _ in seq]
    det = _new(s, spec, direction, status, i0, i1, ck, seq, labels, brk, breakout_price, height,
               target, [_line(s, i0, top(i0), end_x, top(end_x), "קו עליון"),
                        _line(s, i0, bot(i0), end_x, bot(end_x), "קו תחתון")],
               (top(s.last + 1), bot(s.last + 1)) if apex is None or s.last + 1 <= apex else (math.nan, math.nan))
    return det, frozenset(p.i for p in seq)


def _trendline_patterns(s: Series) -> list[tuple[Detection, frozenset]]:
    out = []
    P = s.pivots
    for end in range(len(P) - 1, 3, -1):
        if P[end].i < s.last - TRENDLINE_RECENT_PIVOT_BARS:
            break
        for k in TRENDLINE_SEARCH_PIVOTS:
            if end - k + 1 < 0:
                continue
            found = _trendline_candidate(s, P[end - k + 1:end + 1])
            if found is not None:
                out.append(found)
                break                        # the longest valid pattern for this end
    return out


# ------------------------------------------------------------ flags, pennants
def _consolidation_lines(s: Series, a: int, b: int):
    x = np.arange(a, b + 1, dtype=float)
    bt, at = np.polyfit(x, s.h[a:b + 1], 1)
    bb, ab = np.polyfit(x, s.l[a:b + 1], 1)
    return (lambda i: at + bt * i), (lambda i: ab + bb * i), bt, bb


def _flags(s: Series) -> list[tuple[Detection, frozenset]]:
    out, seen = [], set()
    r = s.rules
    recent = int(r.g("recent_breakout_sessions"))
    for key in ("flag", "pennant"):
        spec = r.chart[key]
        min_d, max_d = int(spec.p("min_duration_sessions")), int(spec.p("max_duration_sessions"))
        pole_max = int(spec.p("pole_max_sessions"))
        for p in range(max(s.last - recent - max_d - 1, pole_max + 1), s.last - min_d + 1):
            for bull in (True, False):
                # pole: from the extreme within `pole_max` sessions before p, to p
                window = slice(p - pole_max, p)
                ps = (p - pole_max + int(np.argmin(s.l[window]))) if bull else \
                     (p - pole_max + int(np.argmax(s.h[window])))
                pole_len = p - ps
                move = (s.h[p] / s.l[ps] - 1) * 100 if bull else (1 - s.l[p] / s.h[ps]) * 100
                if pole_len < 2 or move < spec.p("pole_min_move_pct"):
                    continue
                pole_height = (s.h[p] - s.l[ps]) if bull else (s.h[ps] - s.l[p])
                # "unusually steep": net move per session in ATRs from before the pole
                # (the pole's own wide bars would inflate a later ATR)
                pre_atr = s.atr[ps]
                steepness = pole_height / pole_len / pre_atr if pre_atr and not math.isnan(pre_atr) else 0.0
                if steepness < spec.p("pole_min_atr_per_session"):
                    continue
                # Longest consolidation first: lines fitted to a flag that is only
                # half-built are too narrow, and its next ordinary swing would
                # pass for a breakout.
                for e in range(min(p + max_d, s.last), p + min_d - 1, -1):
                    cons = slice(p + 1, e + 1)
                    if bull and s.h[cons].max() > s.h[p]:
                        continue              # a new high inside: the pole was not over
                    if not bull and s.l[cons].min() < s.l[p]:
                        continue
                    retrace = ((s.h[p] - s.l[cons].min()) if bull else (s.h[cons].max() - s.l[p])) / pole_height
                    if retrace > spec.p("max_retrace_of_pole"):
                        continue
                    upper, lower, b_up, b_lo = _consolidation_lines(s, p + 1, e)
                    w0, w1 = upper(p + 1) - lower(p + 1), upper(e) - lower(e)
                    if w0 <= 0 or w1 <= 0:
                        continue
                    tol = r.g("inside_tolerance_atr") * float(np.nanmedian(s.atr[cons]))
                    idx = np.arange(p + 1, e + 1)
                    if not np.all((s.c[cons] <= upper(idx) + tol) & (s.c[cons] >= lower(idx) - tol)):
                        continue              # price already left the lines inside this window
                    if key == "flag":
                        shape_ok = abs(w1 - w0) / w0 <= spec.p("max_width_change")
                        shape = ("max_width_change", "קווים מקבילים (שינוי רוחב)", abs(w1 - w0) / w0,
                                 f"<= {spec.p('max_width_change')}")
                    else:
                        shape_ok = w1 / w0 <= spec.p("max_end_width_share") and b_up < 0 < b_lo
                        shape = ("max_end_width_share", "קווים מתכנסים (רוחב בסוף/בהתחלה)", w1 / w0,
                                 f"<= {spec.p('max_end_width_share')}")
                    if not shape_ok:
                        continue
                    nxt = e + 1
                    brk = None
                    if nxt <= s.last:
                        if s.c[nxt] > upper(nxt) or s.c[nxt] < lower(nxt):
                            brk = nxt
                        else:
                            continue          # the next close is inside: not the end
                    status = _status(s, e, brk, None)
                    if status is None or (key, bull, ps) in seen:
                        continue
                    seen.add((key, bull, ps))
                    direction = "either" if brk is None else ("bullish" if s.c[brk] > upper(brk) else "bearish")
                    ck = Checks(spec)
                    ck.need("pole_min_move_pct", "תורן: תנועה (%)", move, f">= {spec.p('pole_min_move_pct')}",
                            True, "pole_min_move_pct")
                    ck.need("pole_max_sessions", "תורן: משך (ימי מסחר)", pole_len,
                            f"2-{spec.p('pole_max_sessions'):g}", True, "pole_max_sessions")
                    ck.need("pole_min_atr_per_session", "תורן: תלילות (ATR ליום)", steepness,
                            f">= {spec.p('pole_min_atr_per_session')}", True, "pole_min_atr_per_session")
                    ck.need("duration", "משך ההתכנסות (ימי מסחר)", e - p,
                            f"{min_d}-{max_d}", True, "max_duration_sessions")
                    ck.need("max_retrace_of_pole", "נסיגה מהתורן (חלק מגובהו)", retrace,
                            f"<= {spec.p('max_retrace_of_pole')}", True, "max_retrace_of_pole")
                    ck.need(shape[0], shape[1], shape[2], shape[3], True, shape[0])
                    ck.need("breakout", "פריצה: סגירה מחוץ לקווים", s.c[brk] if brk is not None else None,
                            "outside a line", brk is not None)
                    if brk is None:
                        bp, target = math.nan, math.nan
                    else:
                        bp = upper(brk) if direction == "bullish" else lower(brk)
                        target = bp + pole_height if direction == "bullish" else bp - pole_height
                    end_x = brk if brk is not None else s.last
                    pts = [Pivot(ps, float(s.l[ps] if bull else s.h[ps]), "L" if bull else "H"),
                           Pivot(p, float(s.h[p] if bull else s.l[p]), "H" if bull else "L")]
                    det = _new(s, spec, direction, status, ps, e, ck, pts, ["תחילת התורן", "ראש התורן"],
                               brk, bp, pole_height, target,
                               [_line(s, p + 1, upper(p + 1), end_x, upper(end_x), "קו עליון"),
                                _line(s, p + 1, lower(p + 1), end_x, lower(end_x), "קו תחתון")],
                               (upper(s.last + 1), lower(s.last + 1)))
                    out.append((det, frozenset({ps, p})))
                    break
    return out


def _high_tight_flag(s: Series) -> list[tuple[Detection, frozenset]]:
    spec = s.rules.chart["high_tight_flag"]
    r = s.rules
    max_rise = int(spec.p("max_rise_sessions"))
    max_cons = int(spec.p("max_consolidation_sessions"))
    recent = int(r.g("recent_breakout_sessions"))
    out, seen = [], set()
    for p in range(max(s.last - max_cons - recent, max_rise), s.last):
        lo_i = p - max_rise + int(np.argmin(s.l[p - max_rise:p]))
        rise = (s.h[p] / s.l[lo_i] - 1) * 100
        if rise < spec.p("min_rise_pct") or s.h[lo_i:p].max() > s.h[p]:
            continue
        brk = None
        for e in range(p + 1, min(p + max_cons, s.last) + 1):
            if s.c[e] > s.h[p]:
                brk = e
                break
        cons_end = (brk - 1) if brk is not None else min(p + max_cons, s.last)
        if cons_end <= p:
            continue
        if brk is None and s.last > p + max_cons:
            continue
        retrace = (1 - s.l[p + 1:cons_end + 1].min() / s.h[p]) * 100
        if retrace > spec.p("max_retrace_pct") or lo_i in seen:
            continue
        status = _status(s, cons_end, brk, None)
        if status is None:
            continue
        seen.add(lo_i)
        ck = Checks(spec)
        ck.need("min_rise_pct", "עלייה (%)", rise, f">= {spec.p('min_rise_pct')}", True, "min_rise_pct")
        ck.need("max_rise_sessions", "משך העלייה (ימי מסחר)", p - lo_i, f"<= {max_rise}", True,
                "max_rise_sessions")
        ck.need("max_retrace_pct", "נסיגה בהתכנסות (%)", retrace, f"<= {spec.p('max_retrace_pct')}",
                True, "max_retrace_pct")
        ck.need("confirmation", "אישור: סגירה מעל השיא", s.c[brk] if brk is not None else None,
                f"> {s.h[p]:.4g}", brk is not None)
        height = s.h[p] - s.l[lo_i]
        det = _new(s, spec, "bullish", status, lo_i, cons_end, ck,
                   [Pivot(lo_i, float(s.l[lo_i]), "L"), Pivot(p, float(s.h[p]), "H")],
                   ["תחילת העלייה", "שיא"], brk, float(s.h[p]), height, math.nan,
                   [_line(s, p, s.h[p], brk if brk is not None else s.last, s.h[p], "קו אישור")],
                   (float(s.h[p]), math.nan))
        out.append((det, frozenset({lo_i, p})))
    return out


# ------------------------------------------------------------ cup with handle
def _cup(s: Series) -> list[tuple[Detection, frozenset]]:
    spec = s.rules.chart["cup_with_handle"]
    out = []
    highs = [p for p in s.pivots if p.kind == "H"]
    horizon = s.last - int(s.rules.g("forming_max_age_sessions")) - int(s.rules.g("recent_breakout_sessions")) \
        - int(spec.p("min_handle_sessions"))
    for right in highs:
        if right.i < horizon:
            continue
        best = None
        for left in highs:
            span = right.i - left.i
            if not spec.p("min_cup_sessions") <= span <= spec.p("max_cup_sessions"):
                continue
            ck = Checks(spec)
            lips_low = min(left.price, right.price)
            inner = slice(left.i + 1, right.i)
            bottom = float(s.l[inner].min())
            depth = (1 - bottom / lips_low) * 100
            lower_third = bottom + (lips_low - bottom) / 3
            bottom_share = float(np.mean(s.l[inner] <= lower_third))
            ok = (ck.need("max_lip_diff_pct", "הפרש בין שפות הספל (%)", pct_diff(left.price, right.price),
                          f"<= {spec.p('max_lip_diff_pct')}",
                          pct_diff(left.price, right.price) <= spec.p("max_lip_diff_pct"), "max_lip_diff_pct")
                  and ck.need("below_lips", "הספל כולו מתחת לשפות", float(s.h[inner].max()),
                              f"<= {max(left.price, right.price):.4g}",
                              s.h[inner].max() <= max(left.price, right.price))
                  and ck.need("cup_sessions", "משך הספל (ימי מסחר)", span,
                              f"{spec.p('min_cup_sessions'):g}-{spec.p('max_cup_sessions'):g}", True,
                              "min_cup_sessions")
                  and ck.need("depth_pct", "עומק הספל (%)", depth,
                              f"{spec.p('min_depth_pct'):g}-{spec.p('max_depth_pct'):g}",
                              spec.p("min_depth_pct") <= depth <= spec.p("max_depth_pct"), "min_depth_pct")
                  and ck.need("min_bottom_share", "צורת U: זמן בשליש התחתון", bottom_share,
                              f">= {spec.p('min_bottom_share')}", bottom_share >= spec.p("min_bottom_share"),
                              "min_bottom_share"))
            if ok and (best is None or span > best[0]):
                best = (span, left, bottom, depth, ck)
        if best is None:
            continue
        span, left, bottom, depth, ck = best
        mid = bottom + (min(left.price, right.price) - bottom) / 2
        brk = s.first_close(right.i + 1, s.last, lambda i: s.c[i] > right.price)
        handle_end = (brk - 1) if brk is not None else s.last
        handle_len = handle_end - right.i
        if handle_len < 1:
            continue
        handle_low = float(s.l[right.i + 1:handle_end + 1].min())
        if not (ck.need("min_handle_sessions", "משך הידית (ימי מסחר)", handle_len,
                        f">= {spec.p('min_handle_sessions'):g}", handle_len >= spec.p("min_handle_sessions"),
                        "min_handle_sessions")
                and ck.need("handle_upper_half", "הידית בחצי העליון של הספל", handle_low, f">= {mid:.4g}",
                            handle_low >= mid)):
            continue
        # recency of a cup still forming counts from its right lip (the handle has
        # no maximum length in Bulkowski's guidelines, so it cannot be the clock)
        status = _status(s, right.i, brk, None)
        if status is None:
            continue
        ck.need("confirmation", "אישור: סגירה מעל השפה הימנית", s.c[brk] if brk is not None else None,
                f"> {right.price:.4g}", brk is not None)
        height = min(left.price, right.price) - bottom
        bottom_i = left.i + 1 + int(np.argmin(s.l[left.i + 1:right.i]))
        det = _new(s, spec, "bullish", status, left.i, handle_end, ck,
                   [left, Pivot(bottom_i, bottom, "L"), right], ["שפה שמאלית", "תחתית הספל", "שפה ימנית"],
                   brk, right.price, height, right.price + height,
                   [_line(s, right.i, right.price, brk if brk is not None else s.last, right.price, "קו אישור")],
                   (right.price, math.nan))
        out.append((det, frozenset({left.i, bottom_i, right.i})))
    return out


# ------------------------------------------------------------------ driver
# Larger formations first: a detection sharing two or more turning points with a
# kept one in the same direction is the same move seen twice, and is dropped.
DETECTORS: tuple[Callable[[Series], list], ...] = (
    lambda s: _head_shoulders(s, True), lambda s: _head_shoulders(s, False),
    lambda s: _triple(s, True), lambda s: _triple(s, False),
    _cup, _trendline_patterns,
    lambda s: _double(s, True), lambda s: _double(s, False),
    _high_tight_flag, _flags,
)


def detect_chart(bars: pd.DataFrame, symbol: str, rules: Rules) -> list[Detection]:
    if len(bars) < int(rules.g("atr_period")) + 10:
        return []
    s = Series(bars, rules, symbol)
    kept: list[tuple[Detection, frozenset]] = []
    for detector in DETECTORS:
        for det, used in detector(s):
            clash = any(len(used & other) >= 2 and det.direction == d.direction
                        for d, other in kept)
            if not clash:
                kept.append((det, used))
    return [d for d, _ in kept]
