"""The analyst's chart (SVG): candles and volume with, by default, the simple view
(view.py: the nearest zones, and Fibonacci and the volume-by-price profile when they
matter; the profile sits in a panel beside the price axis). A list of drawing ids draws
those instead (trendlines, divergences, patterns and averages too). Every coordinate comes from the
Analysis (facts.py); the colours are the site's night-violet palette (chart_svg.py).
"""
from __future__ import annotations

import html
import math
from typing import Any

import pandas as pd

from ..channels.chart_svg import ANN, INK, MONO, SANS
from ..indicators import sma
from .facts import Analysis
from .view import note_parts, simple_view

W, H = 1040, 560
LEFT, AXIS, PROFILE, TOP, BOTTOM = 14, 66, 96, 48, 28
GUTTER = 190                    # right of the last candle: the drawings' labels, never over the candles
VOLUME_SHARE = 0.15
SHOW_BARS = 180
MA_COLORS = {50: INK["sma50"], 150: INK["sma150"], 200: "#A78BFA"}
ZONE = {"support": ANN["bull"], "resistance": ANN["bear"]}


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _fmt(price: float) -> str:
    return f"{price:,.2f}"


def _ltr(text: str) -> str:
    """Numbers and ranges kept left-to-right inside a Hebrew label (Unicode isolates)."""
    return f"\u2066{text}\u2069"


class _Plot:
    def __init__(self, first: int, count: int, lo: float, hi: float):
        self.first, self.count, self.lo, self.hi = first, count, lo, hi
        self.x0, self.x1 = LEFT, W - AXIS - PROFILE - 6
        self.xc = self.x1 - GUTTER                               # the last candle ends here
        plot = H - TOP - BOTTOM
        self.y0, self.y1 = TOP, TOP + plot * (1 - VOLUME_SHARE) - 6
        self.v0, self.v1 = self.y1 + 10, H - BOTTOM
        self.step = (self.xc - self.x0) / (count + 2)

    def x(self, i: float) -> float:
        return self.x0 + (i - self.first + 0.5) * self.step

    def y(self, price: float) -> float:
        return self.y0 + (self.hi - price) / (self.hi - self.lo) * (self.y1 - self.y0)

    def inside(self, price: float) -> bool:
        return self.lo <= price <= self.hi


class _Tags:
    """Small labels that never cover each other."""

    def __init__(self, plot: _Plot):
        self.plot, self.out, self.boxes = plot, [], []

    def add(self, x: float, y: float, text: str, color: str, anchor: str = "start") -> None:
        """A label in the gutter right of the candles (`x` only picks the gutter's side)."""
        width = 16 + 6.9 * len(text.replace("\u2066", "").replace("\u2069", ""))
        text = f"\u2067{text}\u2069"             # a right-to-left label, whatever it starts or ends with
        left = self.plot.xc + 8 if anchor != "middle" else min(max(x - width / 2, self.plot.x0 + 2),
                                                                    self.plot.xc - width - 4)
        lowest, highest = self.plot.y0 + 11, self.plot.y1 - 11
        wanted = y
        y = min(max(y, lowest), highest)
        # the free height nearest to the wanted one, anywhere in the column
        steps = int((highest - lowest) // 4) + 1
        for cy in sorted((lowest + 4 * k for k in range(steps)), key=lambda c: abs(c - y)):
            if all(left + width + 3 <= b[0] or left - 3 >= b[2] or cy + 12 <= b[1] or cy - 12 >= b[3]
                   for b in self.boxes):
                y = cy
                break
        self.boxes.append((left, y - 10, left + width, y + 10))
        if anchor != "middle" and abs(y - wanted) > 8 and self.plot.y0 <= wanted <= self.plot.y1:
            # moved away from its level: a thin leader keeps them paired
            self.out.append(f'<path class="ann" d="M{self.plot.xc + 2:.1f},{wanted:.1f} '
                            f'L{left - 2:.1f},{y:.1f}" stroke="{color}" stroke-width="0.8" '
                            f'stroke-opacity="0.7" fill="none"/>')
        self.out.append(f'<g class="ann"><rect x="{left:.1f}" y="{y - 10:.1f}" width="{width:.1f}" '
                        f'height="20" rx="6" fill="{ANN["tag"]}" stroke="{color}" stroke-width="1"/>'
                        f'<text x="{left + width / 2:.1f}" y="{y + 4:.1f}" fill="{color}" '
                        f'font-family="{SANS}" font-size="12" font-weight="600" '
                        f'text-anchor="middle">{_esc(text)}</text></g>')


def render(bars: pd.DataFrame, analysis: Analysis, drawings: list[str] | None = None, *,
           title: str | None = None) -> str:
    if drawings is None:
        items = {k: v for k, v in simple_view(analysis).items() if v.get("show", True)}
    else:
        items = {k: analysis.drawings[k] for k in drawings if k in analysis.drawings}
    bars = bars.reset_index(drop=True)
    last = len(bars) - 1
    first = max(0, len(bars) - SHOW_BARS)
    win = bars.iloc[first:]
    days = bars["timestamp"].dt.strftime("%Y-%m-%d").tolist()
    index = {d: i for i, d in enumerate(days)}
    lo, hi = float(win["low"].min()), float(win["high"].max())
    span = hi - lo or 1.0
    near = (lo - 0.3 * span, hi + 0.3 * span)      # levels farther away stay facts, not drawings

    def keep(price: Any) -> bool:
        try:
            return near[0] <= float(price) <= near[1]
        except (TypeError, ValueError):
            return False

    levels = []
    for item in items.values():
        if item["type"] == "zone":
            levels += [item["low"], item["high"]]
        elif item["type"] == "fib":
            levels += [v for v in item["levels"].values()]
        elif item["type"] == "pattern":
            levels += [item.get("target"), item.get("invalidation")]
    levels = [float(v) for v in levels if keep(v)]
    lo, hi = min([lo, *levels]), max([hi, *levels])
    pad = (hi - lo) * 0.06 or 1.0
    plot = _Plot(first, len(win), lo - pad, hi + pad)
    tags = _Tags(plot)
    right = plot.xc + 4
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" class="shot-svg" role="img" '
           f'aria-label="{_esc(title or analysis.symbol)}" direction="ltr">',
           f'<rect width="{W}" height="{H}" rx="12" fill="{INK["bg"]}"/>']

    for k in range(7):                                      # grid and price scale
        price = plot.lo + (plot.hi - plot.lo) * k / 6
        y = plot.y(price)
        out.append(f'<line x1="{plot.x0}" x2="{plot.x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{INK["grid"]}"/>')
        out.append(f'<text x="{W - AXIS + 6}" y="{y + 4:.1f}" fill="{INK["axis"]}" font-family="{MONO}" '
                   f'font-size="12">{_fmt(price)}</text>')
    for k in range(6):
        i = first + int(k * (len(win) - 1) / 5)
        out.append(f'<text x="{plot.x(i):.1f}" y="{H - 9}" fill="{INK["axis"]}" font-family="{MONO}" '
                   f'font-size="12" text-anchor="middle">{pd.Timestamp(days[i]):%d/%m/%y}</text>')
    out.append(f'<text x="{plot.x0 + 4}" y="28" fill="{INK["title"]}" font-family="{MONO}" '
               f'font-size="15" font-weight="600">{_esc(title or analysis.symbol)}</text>')
    out.append(f'<text x="{W - AXIS - PROFILE}" y="28" fill="{INK["axis"]}" font-family="{MONO}" '
               f'font-size="12" text-anchor="end">1D · {pd.Timestamp(days[last]):%d/%m/%Y}</text>')

    # zones first (behind the candles)
    for item in items.values():
        if item["type"] != "zone" or not (keep(item["low"]) and keep(item["high"])):
            continue
        color = ZONE[item["kind"]]
        x_a = plot.x(max(first, index.get(item["first_day"], first))) - plot.step
        y_a, y_b = plot.y(item["high"]), plot.y(item["low"])
        out.append(f'<rect class="ann" x="{x_a:.1f}" y="{y_a:.1f}" width="{right - x_a:.1f}" '
                   f'height="{max(2.0, y_b - y_a):.1f}" fill="{color}" fill-opacity="0.13" '
                   f'stroke="{color}" stroke-opacity="0.45" stroke-dasharray="4 3"/>')
        name = "תמיכה" if item["kind"] == "support" else "התנגדות"
        notes = "".join(f" · {word}" + (f" {_ltr(number)}" if number else "")
                        for word, number in map(note_parts, item.get("notes", [])))
        tags.add(right, (y_a + y_b) / 2,
                 f"{name} {_ltr(_fmt(item['low']) + '–' + _fmt(item['high']))}{notes}", color)

    # volume and candles
    vmax = float(win["volume"].max() or 1)
    body = max(1.0, plot.step * 0.62)
    for i, row in zip(range(first, last + 1), win.itertuples(index=False)):
        x = plot.x(i)
        up = row.close >= row.open
        color = INK["up"] if up else INK["down"]
        vh = (plot.v1 - plot.v0) * (float(row.volume) / vmax if vmax else 0)
        out.append(f'<rect x="{x - body / 2:.1f}" y="{plot.v1 - vh:.1f}" width="{body:.1f}" '
                   f'height="{max(0.5, vh):.1f}" fill="{color}" fill-opacity="0.35"/>')
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{plot.y(row.high):.1f}" y2="{plot.y(row.low):.1f}" '
                   f'stroke="{color}" stroke-width="1"/>')
        top, bot = plot.y(max(row.open, row.close)), plot.y(min(row.open, row.close))
        out.append(f'<rect x="{x - body / 2:.1f}" y="{top:.1f}" width="{body:.1f}" '
                   f'height="{max(1.0, bot - top):.1f}" fill="{color}"/>')

    if "ma" in items:
        for n in items["ma"]["periods"]:
            values = sma(bars["close"], n).iloc[first:]
            pts = [f"{plot.x(i):.1f},{plot.y(v):.1f}" for i, v in zip(range(first, last + 1), values)
                   if math.isfinite(v) and plot.inside(v)]
            if len(pts) > 1:
                out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{MA_COLORS.get(n, INK["axis"])}" '
                           f'stroke-width="1.4" stroke-opacity="0.85"/>')

    for item in items.values():
        kind = item["type"]
        if kind == "line":
            i1 = index.get(item["day1"], first)
            a_i, b_i = max(i1, first), last + 2
            slope = (item["price2"] - item["price1"]) / max(1, last - i1)
            ya = item["price1"] + slope * (a_i - i1)
            yb = item["price1"] + slope * (b_i - i1)
            color = ZONE[item["kind"]]
            out.append(f'<line class="ann" x1="{plot.x(a_i):.1f}" y1="{plot.y(ya):.1f}" x2="{plot.x(b_i):.1f}" '
                       f'y2="{plot.y(yb):.1f}" stroke="{color}" stroke-width="2.2" stroke-linecap="round"/>')
            name = "קו תמיכה" if item["kind"] == "support" else "קו התנגדות"
            tags.add(right, plot.y(yb), name, color)
        elif kind == "fib":
            a_i = max(first, index.get(item["start_day"], first))
            b_i = index.get(item["end_day"], last)
            if drawings is not None:                        # the swing itself: not in the simple view
                out.append(f'<line class="ann" x1="{plot.x(a_i):.1f}" y1="{plot.y(item["start"]):.1f}" '
                           f'x2="{plot.x(b_i):.1f}" y2="{plot.y(item["end"]):.1f}" stroke="{ANN["line"]}" '
                           f'stroke-width="1.2" stroke-dasharray="2 4"/>')
            for name, price in item["levels"].items():
                if not keep(price):
                    continue
                ratio = int(name.rsplit("_", 1)[1]) / 10
                y = plot.y(price)
                out.append(f'<line class="ann" x1="{plot.x(b_i):.1f}" y1="{y:.1f}" x2="{right:.1f}" y2="{y:.1f}" '
                           f'stroke="{ANN["target"]}" stroke-width="1" stroke-opacity="0.8" stroke-dasharray="6 4"/>')
                tags.add(right, y, f"פיבו {_ltr(f'{ratio:g}%')} {_ltr(_fmt(price))}", ANN["target"])
        elif kind == "divergence":
            i1, i2 = index.get(item["day1"]), index.get(item["day2"])
            if i1 is None or i2 is None or i1 < first:
                continue
            color = ANN["bull"] if item["kind"] == "bullish" else ANN["bear"]
            out.append(f'<line class="ann" x1="{plot.x(i1):.1f}" y1="{plot.y(item["price1"]):.1f}" '
                       f'x2="{plot.x(i2):.1f}" y2="{plot.y(item["price2"]):.1f}" stroke="{color}" '
                       f'stroke-width="2" stroke-dasharray="5 3"/>')
            for i, price in ((i1, item["price1"]), (i2, item["price2"])):
                out.append(f'<circle class="ann" cx="{plot.x(i):.1f}" cy="{plot.y(price):.1f}" r="4.5" '
                           f'fill="{INK["bg"]}" stroke="{color}" stroke-width="2"/>')
            name = "סטייה שורית" if item["kind"] == "bullish" else "סטייה דובית"
            tags.add(plot.x(i2), plot.y(item["price2"]) + (22 if item["kind"] == "bullish" else -22),
                     f"{name} {item['indicator'].upper()}", color, "middle")
        elif kind == "pattern" and item["family"] == "chart":
            color = ANN["bull"] if item["direction"] == "bullish" else ANN["bear"] if item["direction"] == "bearish" else ANN["line"]
            for line in item["lines"]:
                i1, i2 = index.get(line["x1"]), index.get(line["x2"])
                if i1 is None or i2 is None or i2 < first:
                    continue
                out.append(f'<line class="ann" x1="{plot.x(max(i1, first)):.1f}" y1="{plot.y(line["y1"]):.1f}" '
                           f'x2="{plot.x(i2):.1f}" y2="{plot.y(line["y2"]):.1f}" stroke="{ANN["line"]}" '
                           f'stroke-width="1.8"/>')
            for point in item["points"]:
                i = index.get(point["date"])
                if i is not None and i >= first:
                    out.append(f'<circle class="ann" cx="{plot.x(i):.1f}" cy="{plot.y(point["price"]):.1f}" '
                               f'r="4" fill="{INK["bg"]}" stroke="{ANN["line"]}" stroke-width="1.8"/>')
            for level, name, dash in ((item.get("target"), "יעד (כלל המדידה)", "7 4"),
                                      (item.get("invalidation"), "ביטול", "2 4")):
                if level is not None and keep(level):
                    y = plot.y(level)
                    out.append(f'<line class="ann" x1="{plot.x(max(first, index.get(item["end"], first))):.1f}" '
                               f'y1="{y:.1f}" x2="{right:.1f}" y2="{y:.1f}" stroke="{color}" '
                               f'stroke-width="1.4" stroke-dasharray="{dash}"/>')
                    tags.add(right, y, f"{name} {_ltr(_fmt(level))}", color)
        elif kind == "pattern":                           # a candlestick signal: a marker
            i = index.get(item["end"])
            if i is not None and i >= first:
                color = ANN["bull"] if item["direction"] == "bullish" else ANN["bear"]
                y = plot.y(float(bars["low"].iloc[i])) + 14 if item["direction"] == "bullish" \
                    else plot.y(float(bars["high"].iloc[i])) - 14
                out.append(f'<circle class="ann" cx="{plot.x(i):.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')

    if "vp" in items:                                      # the profile panel
        vp = items["vp"]
        px0, px1 = plot.x1 + 8, plot.x1 + 8 + PROFILE - 12
        vmax = max(vp["volume"]) or 1
        for n, vol in enumerate(vp["volume"]):
            y_top, y_bot = plot.y(vp["edges"][n + 1]), plot.y(vp["edges"][n])
            inside = vp["val"] <= vp["edges"][n] < vp["vah"]
            width = (px1 - px0) * vol / vmax
            out.append(f'<rect x="{px0:.1f}" y="{y_top:.1f}" width="{width:.1f}" height="{max(1.0, y_bot - y_top - 1):.1f}" '
                       f'fill="{ANN["accent"]}" fill-opacity="{0.55 if inside else 0.22}"/>')
        y = plot.y(vp["poc"])
        out.append(f'<line class="ann" x1="{plot.x0:.1f}" y1="{y:.1f}" x2="{px1:.1f}" y2="{y:.1f}" '
                   f'stroke="{ANN["accent"]}" stroke-width="1" stroke-opacity="0.7" stroke-dasharray="1 3"/>')
        out.append(f'<text x="{(px0 + px1) / 2:.1f}" y="{TOP - 8}" fill="{INK["axis"]}" font-family="{SANS}" '
                   f'font-size="11" text-anchor="middle">נפח לפי מחיר</text>')
        if vp.get("poc_label", True):
            tags.add(right, y, f"שליטה (POC) {_ltr(_fmt(vp['poc']))}", ANN["accent"])

    out += tags.out
    out.append("</svg>")
    return "".join(out)
