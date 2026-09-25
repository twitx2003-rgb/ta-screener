"""The analyst's chart (SVG): candles and volume with, by default, the simple view
(view.py: the nearest zones, and Fibonacci and the volume-by-price profile when they
matter; the profile sits in a panel beside the price axis). A list of drawing ids draws
those instead (trendlines, divergences, patterns and averages too). Every coordinate comes from the
Analysis (facts.py); the colours are the site's night-violet palette (chart_svg.py).
"""
from __future__ import annotations

import html
import math
import re
from typing import Any

import pandas as pd

from ..channels.chart_svg import ANN, INK, MONO, SANS
from ..indicators import sma
from .facts import Analysis
from .view import note_parts, simple_view

W, H = 1040, 560
LEFT, AXIS, PROFILE, TOP, BOTTOM = 14, 66, 96, 48, 28
GUTTER = 220                    # right of the last candle: the drawings' labels, never over the candles
                                # (a zone label with a note is ~210 px: it stays clear of the profile)
VOLUME_SHARE = 0.15
SHOW_BARS = 130                 # about six months: wide enough candles on a phone
MAX_BARS = 250                  # a named pattern that started earlier widens the window up to this
PATTERN_LEVEL_ATR = 3.0         # a pattern's cancel level farther than this is left off the chart
MA_COLORS = {50: INK["sma150"], 150: "#F0ABFC", 200: "#A78BFA"}   # never Fibonacci's gold
ZONE = {"support": ANN["bull"], "resistance": ANN["bear"]}


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _fmt(price: float) -> str:
    return f"{price:,.2f}"


def _ltr(text: str) -> str:
    """Numbers and ranges kept left-to-right inside a Hebrew label (Unicode isolates)."""
    return f"\u2066{text}\u2069"


def _ticks(lo: float, hi: float, count: int = 6) -> tuple[list[float], float]:
    """Round price-axis values (steps of 1, 2, 2.5 or 5 times a power of ten)."""
    raw = max((hi - lo) / count, 1e-9)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = min((m * magnitude for m in (1, 2, 2.5, 5, 10)), key=lambda s: abs(s - raw))
    values, v = [], math.ceil(lo / step) * step
    while v <= hi + step * 1e-6:
        values.append(round(v, 10))
        v += step
    return values, step


def _tick_label(value: float, step: float) -> str:
    decimals = 0
    while decimals < 4 and abs(step * 10 ** decimals - round(step * 10 ** decimals)) > 1e-6:
        decimals += 1
    return f"{value:,.{decimals}f}"


def _company(name: str) -> str:
    """The header's company name without "(The)" or a trailing Inc./Corp. (review round 2)."""
    name = re.sub(r"\s*\(The\)|,?\s+(?:Inc|Corp|Corporation|Ltd|plc)\.?$", "", str(name)).strip()
    return name if len(name) <= 42 else name[:41].rstrip() + "…"


def _finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


class _Plot:
    def __init__(self, first: int, count: int, lo: float, hi: float, profile: bool = True):
        self.first, self.count, self.lo, self.hi = first, count, lo, hi
        # the volume-profile panel takes its room only when the profile is drawn
        self.x0, self.x1 = LEFT, W - AXIS - (PROFILE if profile else 0) - 6
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
    """Small labels that never cover each other. The gutter labels keep their price order
    and stay inside the gutter, clear of the price axis (review round 1: labels swapped
    places and hid axis values); a label pushed off its level gets a thin leader."""

    GAP = 24                                 # a label is 20 px high

    def __init__(self, plot: _Plot):
        self.plot, self.out, self.boxes = plot, [], []
        self.pending: list[tuple[float, str, str]] = []

    def add(self, x: float, y: float, text: str, color: str, anchor: str = "start") -> None:
        if anchor != "middle":
            self.pending.append((y, text, color))     # laid out together in finish()
            return
        width = 16 + 6.9 * len(text.replace("\u2066", "").replace("\u2069", ""))
        left = min(max(x - width / 2, self.plot.x0 + 2), self.plot.xc - width - 4)
        y = min(max(y, self.plot.y0 + 11), self.plot.y1 - 11)
        self.boxes.append((left, y - 10, left + width, y + 10))
        self._draw(left, y, width, 12.0, text, color)

    def finish(self) -> list[str]:
        """Place the gutter labels: in price order, each as near its level as the ones
        above it allow, pushed up from the bottom if the column overflows."""
        lowest, highest = self.plot.y0 + 11, self.plot.y1 - 11
        tags: list[tuple[float, str, str]] = []
        for y, text, color in sorted(self.pending, key=lambda t: t[0]):
            if tags and y - tags[-1][0] < 10:            # two levels at one height: one label
                tags[-1] = ((tags[-1][0] + y) / 2, f"{tags[-1][1]} · {text}", tags[-1][2])
            else:
                tags.append((y, text, color))
        ys: list[float] = []
        for wanted, _, _ in tags:
            y = max(min(max(wanted, lowest), highest), (ys[-1] + self.GAP) if ys else lowest)
            ys.append(y)
        for k in range(len(ys) - 1, -1, -1):          # the bottom overflowed: push back up
            limit = highest if k == len(ys) - 1 else ys[k + 1] - self.GAP
            ys[k] = max(min(ys[k], limit), lowest)
        left = self.plot.xc + 8
        room = self.plot.x1 - left - 2                # never into the profile or the axis
        for (wanted, text, color), y in zip(tags, ys):
            chars = len(text.replace("\u2066", "").replace("\u2069", ""))
            size = 12.0 if 16 + 6.9 * chars <= room else max(9.5, 12.0 * (room - 16) / (6.9 * chars))
            width = min(room, 16 + 6.9 * chars * size / 12.0)
            if abs(y - wanted) > 8 and self.plot.y0 <= wanted <= self.plot.y1:
                self.out.append(f'<path class="ann" d="M{self.plot.xc + 2:.1f},{wanted:.1f} '
                                f'L{left - 2:.1f},{y:.1f}" stroke="{color}" stroke-width="0.8" '
                                f'stroke-opacity="0.7" fill="none"/>')
            self._draw(left, y, width, size, text, color)
        self.pending = []
        return self.out

    def _draw(self, left: float, y: float, width: float, size: float, text: str, color: str) -> None:
        text = f"\u2067{text}\u2069"             # a right-to-left label, whatever it starts or ends with
        self.out.append(f'<g class="ann"><rect x="{left:.1f}" y="{y - 10:.1f}" width="{width:.1f}" '
                        f'height="20" rx="6" fill="{ANN["tag"]}" stroke="{color}" stroke-width="1"/>'
                        f'<text x="{left + width / 2:.1f}" y="{y + 4:.1f}" fill="{color}" '
                        f'font-family="{SANS}" font-size="{size:.1f}" font-weight="600" '
                        f'text-anchor="middle">{_esc(text)}</text></g>')


def render(bars: pd.DataFrame, analysis: Analysis, drawings: list[str] | None = None, *,
           title: str | None = None, name: str | None = None, cited: set[str] | None = None) -> str:
    """The chart. `name` (the company's) goes beside the ticker in the header. `cited`, the
    fact keys the written text relies on, makes the chart draw what the text names (review
    round 2): Fibonacci and the volume profile only when a section cites them, and every
    chart pattern the text names."""
    facts = analysis.facts
    fact = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    if drawings is None:
        items = {k: v for k, v in simple_view(analysis).items() if v.get("show", True)}
        if cited is not None:
            if not any(c.startswith("fib") for c in cited):
                items.pop("fib", None)
            if not any(c.startswith("vp.") for c in cited):
                items.pop("vp", None)
            for key in sorted({c.split(".")[0] for c in cited if c.startswith("pat_")}):
                item = analysis.drawings.get(key)
                if item and item.get("family") == "chart":
                    items[key] = item
    else:
        items = {k: analysis.drawings[k] for k in drawings if k in analysis.drawings}
    bars = bars.reset_index(drop=True)
    last = len(bars) - 1
    days = bars["timestamp"].dt.strftime("%Y-%m-%d").tolist()
    index = {d: i for i, d in enumerate(days)}
    first = max(0, len(bars) - SHOW_BARS)
    for item in items.values():                  # a pattern is shown whole (review round 2)
        if item.get("type") == "pattern" and item.get("family") == "chart" and item.get("start") in index:
            first = max(0, len(bars) - MAX_BARS, min(first, index[item["start"]] - 5))
    win = bars.iloc[first:]
    atr = float(fact("atr") or math.nan)
    close = float(bars["close"].iloc[-1])

    def pattern_levels(key: str, item: dict[str, Any]) -> dict[str, float]:
        """The target, unless the pattern is still forming, failed, doubtful or already
        there; the cancel level, when it is near enough to matter."""
        state = str(fact(f"{key}.state") or "")
        out: dict[str, float] = {}
        target, cancel_at = item.get("target"), item.get("invalidation")
        if (_finite(target) and item.get("status") == "breakout" and not fact(f"{key}.target_reached_day")
                and "נכשלה" not in state and "בספק" not in state):
            out["target"] = float(target)
        if _finite(cancel_at) and (not math.isfinite(atr) or abs(float(cancel_at) - close) <= PATTERN_LEVEL_ATR * atr):
            out["invalidation"] = float(cancel_at)
        return out
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
        elif item["type"] == "extreme":
            levels.append(item["price"])
    for key, item in items.items():
        if item["type"] == "pattern" and item.get("family") == "chart":
            levels += list(pattern_levels(key, item).values())
    levels = [float(v) for v in levels if keep(v)]
    lo, hi = min([lo, *levels]), max([hi, *levels])
    pad = (hi - lo) * 0.06 or 1.0
    plot = _Plot(first, len(win), lo - pad, hi + pad, profile="vp" in items)
    tags = _Tags(plot)
    right = plot.xc + 4
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" class="shot-svg" role="img" '
           f'aria-label="{_esc(title or analysis.symbol)}" direction="ltr">',
           f'<rect width="{W}" height="{H}" rx="12" fill="{INK["bg"]}"/>']

    change = fact("change_1d_pct")
    day_color = INK["down"] if _finite(change) and float(change) < 0 else INK["up"]
    close_y = plot.y(close)
    ticks, step = _ticks(plot.lo, plot.hi)
    for price in ticks:                                     # grid and a round price scale
        y = plot.y(price)
        out.append(f'<line x1="{plot.x0}" x2="{plot.x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{INK["grid"]}"/>')
        if abs(y - close_y) > 14:                           # never under the close's tag
            out.append(f'<text x="{W - AXIS + 6}" y="{y + 4:.1f}" fill="{INK["axis"]}" font-family="{MONO}" '
                       f'font-size="12">{_tick_label(price, step)}</text>')
    for k in range(6):
        i = first + int(k * (len(win) - 1) / 5)
        anchor, x = ("start", plot.x0) if k == 0 else ("middle", plot.x(i))
        out.append(f'<text x="{x:.1f}" y="{H - 9}" fill="{INK["axis"]}" font-family="{MONO}" '
                   f'font-size="12" text-anchor="{anchor}">{pd.Timestamp(days[i]):%d/%m/%y}</text>')
    # the header: ticker and name; close, the day's change, the date
    ticker = analysis.symbol.split(":")[-1]
    out.append(f'<text x="{plot.x0 + 4}" y="25" fill="{INK["title"]}" font-family="{MONO}" '
               f'font-size="17" font-weight="700">{_esc(ticker)}</text>')
    if name:
        out.append(f'<text x="{plot.x0 + 16 + 10.5 * len(ticker):.1f}" y="25" fill="{INK["axis"]}" '
                   f'font-family="{SANS}" font-size="13">{_esc(_company(name))}</text>')
    sub = (f'<tspan fill="{INK["title"]}">{_fmt(close)}</tspan>'
           + (f'  <tspan fill="{day_color}">{float(change):+.2f}%</tspan>' if _finite(change) else "")
           + f'  ·  1D  ·  {pd.Timestamp(days[last]):%d/%m/%Y}')
    out.append(f'<text x="{plot.x0 + 4}" y="43" fill="{INK["axis"]}" font-family="{MONO}" '
               f'font-size="12" xml:space="preserve">{sub}</text>')
    # the legend (right to left, as it is read): only what is drawn, on a second row when
    # the first reaches the company's name
    kinds = {v.get("kind") for v in items.values() if v.get("type") == "zone"}
    periods = (items.get("ma") or {}).get("periods", [])
    extremes = [v["label"] for v in items.values() if v["type"] == "extreme"]
    legend = [(ZONE["support"], "תמיכה") if "support" in kinds else None,
              (ZONE["resistance"], "התנגדות") if "resistance" in kinds else None,
              *[(MA_COLORS[n], f"ממוצע {n} יום") for n in periods if n in MA_COLORS],
              (ANN["line"], "תבנית") if any(v["type"] == "pattern" and v.get("family") == "chart"
                                            for v in items.values()) else None,
              (INK["title"], " / ".join(extremes)) if extremes else None,
              (ANN["target"], "פיבונאצ'י") if "fib" in items else None,
              (ANN["accent"], "נפח לפי מחיר") if "vp" in items else None]
    header_end = plot.x0 + 30 + 10.5 * len(ticker) + (7.2 * len(_company(name)) if name else 0)
    rows = [(21, 25, header_end), (39, 43, plot.x0 + 340)]
    row, cursor = 0, W - 18
    for color, word in (e for e in legend if e):
        width = 13 + 7.2 * len(word) + 16
        if cursor - width < rows[row][2] and row + 1 < len(rows):
            row, cursor = row + 1, W - 18
        cy, ty, _ = rows[row]
        out.append(f'<circle cx="{cursor - 4:.1f}" cy="{cy}" r="4.5" fill="{color}"/>')
        out.append(f'<text x="{cursor - 13:.1f}" y="{ty}" fill="{INK["axis"]}" font-family="{SANS}" '
                   f'font-size="12" text-anchor="end">{_esc(word)}</text>')
        cursor -= width
    # the last close: a dotted line across and a tag on the price axis, in a neutral colour
    # (review round 1: a red or green price tag read as a signal)
    out.append(f'<line x1="{plot.x0}" x2="{plot.x1}" y1="{close_y:.1f}" y2="{close_y:.1f}" stroke="{INK["axis"]}" '
               f'stroke-width="1" stroke-opacity="0.7" stroke-dasharray="2 3"/>')
    out.append(f'<rect x="{W - AXIS + 2}" y="{close_y - 10:.1f}" width="{AXIS - 6}" height="20" rx="4" '
               f'fill="{INK["title"]}"/>')
    out.append(f'<text x="{W - AXIS + 2 + (AXIS - 6) / 2:.1f}" y="{close_y + 4:.1f}" fill="{INK["bg"]}" '
               f'font-family="{MONO}" font-size="12" font-weight="700" text-anchor="middle">{_fmt(close)}</text>')

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
                width, opacity = (1.6, 0.9) if n == 50 else (1.0, 0.65)
                out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{MA_COLORS.get(n, INK["axis"])}" '
                           f'stroke-width="{width}" stroke-opacity="{opacity}"/>')

    for key, item in items.items():
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
            if (item["kind"] == "support") == (item["price2"] > close):     # the price crossed it
                name += " (נחצה)"
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
                tags.add(right, y, f"פיבונאצ'י {_ltr(f'{ratio:g}%')} · {_ltr(_fmt(price))}", ANN["target"])
        elif kind == "extreme":
            if not keep(item["price"]):
                continue
            y = plot.y(item["price"])
            a_i = max(first, index.get(item.get("day") or "", first))
            out.append(f'<line class="ann" x1="{plot.x(a_i):.1f}" y1="{y:.1f}" x2="{right:.1f}" y2="{y:.1f}" '
                       f'stroke="{INK["title"]}" stroke-width="1.2" stroke-opacity="0.7" stroke-dasharray="8 4"/>')
            tags.add(right, y, f"{item['label']} {_ltr(_fmt(item['price']))}", INK["title"])
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
            color = ANN["line"]                             # the pattern's own colour (review round 2)
            tol = 0.5 * atr if math.isfinite(atr) else 0.015 * close
            for line in item["lines"]:
                i1, i2 = index.get(line["x1"]), index.get(line["x2"])
                if i1 is None or i2 is None or i2 < first or i2 == i1:
                    continue
                slope = (line["y2"] - line["y1"]) / (i2 - i1)
                touches = [index[pt["date"]] for pt in item["points"] if pt["date"] in index
                           and i1 <= index[pt["date"]] <= i2
                           and abs(pt["price"] - (line["y1"] + slope * (index[pt["date"]] - i1))) <= tol]
                a_i = max(min(touches) if touches else i1, first)
                ya = line["y1"] + slope * (a_i - i1)
                out.append(f'<line class="ann" x1="{plot.x(a_i):.1f}" y1="{plot.y(ya):.1f}" '
                           f'x2="{plot.x(i2):.1f}" y2="{plot.y(line["y2"]):.1f}" stroke="{color}" stroke-width="1.8"/>')
            for point in item["points"]:
                i = index.get(point["date"])
                if i is not None and i >= first:
                    out.append(f'<circle class="ann" cx="{plot.x(i):.1f}" cy="{plot.y(point["price"]):.1f}" '
                               f'r="4" fill="{INK["bg"]}" stroke="{color}" stroke-width="1.8"/>')
            b = index.get(item.get("breakout_day") or "")
            if b is not None and b >= first and _finite(item.get("breakout")):
                up = item["direction"] == "bullish"
                now = fact(f"{key}.line_now")
                if _finite(now) and b < last:              # the broken line, carried to today, dashed
                    out.append(f'<line class="ann" x1="{plot.x(b):.1f}" y1="{plot.y(float(item["breakout"])):.1f}" '
                               f'x2="{plot.x(last):.1f}" y2="{plot.y(float(now)):.1f}" stroke="{color}" '
                               f'stroke-width="1.3" stroke-dasharray="5 4" stroke-opacity="0.8"/>')
                mark = ANN["bull"] if up else ANN["bear"]
                x = plot.x(b)
                if up:                                     # below the candle: never over it
                    y = plot.y(float(bars["low"].iloc[b])) + 10
                    tri = f"M{x:.1f},{y:.1f} L{x - 6:.1f},{y + 9:.1f} L{x + 6:.1f},{y + 9:.1f} Z"
                else:
                    y = plot.y(float(bars["high"].iloc[b])) - 10
                    tri = f"M{x:.1f},{y:.1f} L{x - 6:.1f},{y - 9:.1f} L{x + 6:.1f},{y - 9:.1f} Z"
                out.append(f'<path class="ann" d="{tri}" fill="{mark}"/>')
                day = days[b]
                text = f"{'פריצה' if up else 'שבירה'} {_ltr(day[8:10] + '/' + day[5:7])}"
                # the label goes where no candle is (review round 2: it covered the last
                # candles); with no free place the marker stands alone
                half = (16 + 6.9 * 11) / 2
                span = [i for i in range(first, last + 1) if abs(plot.x(i) - x) <= half + plot.step]
                for offset in (26, 50, 74, 98):
                    label_y = y + offset if up else y - offset
                    if not plot.y0 + 11 <= label_y <= plot.y1 - 11:
                        break
                    if all(plot.y(float(bars["low"].iloc[i])) < label_y - 12 or
                           plot.y(float(bars["high"].iloc[i])) > label_y + 12 for i in span):
                        if offset > 26:                    # a thin leader back to the marker
                            tip, edge = (y + 9, label_y - 10) if up else (y - 9, label_y + 10)
                            out.append(f'<line class="ann" x1="{x:.1f}" y1="{tip:.1f}" x2="{x:.1f}" '
                                       f'y2="{edge:.1f}" stroke="{mark}" stroke-width="0.8" stroke-opacity="0.7"/>')
                        tags.add(x, label_y, text, mark, "middle")
                        break
            names = {"target": ("יעד (גובה התבנית)", "7 4"), "invalidation": ("ביטול התבנית", "2 4")}
            for which, level in pattern_levels(key, item).items():
                if not keep(level):
                    continue
                label, dash = names[which]
                y = plot.y(level)
                out.append(f'<line class="ann" x1="{plot.x(max(first, index.get(item["end"], first))):.1f}" '
                           f'y1="{y:.1f}" x2="{right:.1f}" y2="{y:.1f}" stroke="{color}" '
                           f'stroke-width="1.4" stroke-dasharray="{dash}"/>')
                tags.add(right, y, f"{label} {_ltr(_fmt(level))}", color)
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
            # the profile covers the whole analysed year; the chart shows less of it
            if vp["edges"][n] >= plot.hi or vp["edges"][n + 1] <= plot.lo:
                continue
            y_top, y_bot = plot.y(min(vp["edges"][n + 1], plot.hi)), plot.y(max(vp["edges"][n], plot.lo))
            inside = vp["val"] <= vp["edges"][n] < vp["vah"]
            width = (px1 - px0) * vol / vmax
            out.append(f'<rect x="{px0:.1f}" y="{y_top:.1f}" width="{width:.1f}" height="{max(1.0, y_bot - y_top - 1):.1f}" '
                       f'fill="{ANN["accent"]}" fill-opacity="{0.55 if inside else 0.22}"/>')
        y = plot.y(vp["poc"])
        out.append(f'<line class="ann" x1="{plot.x0:.1f}" y1="{y:.1f}" x2="{px1:.1f}" y2="{y:.1f}" '
                   f'stroke="{ANN["accent"]}" stroke-width="1" stroke-opacity="0.7" stroke-dasharray="1 3"/>')
        if vp.get("poc_label", True):
            tags.add(right, y, f"נפח מרבי {_ltr(_fmt(vp['poc']))}", ANN["accent"])

    out += tags.finish()
    out.append("</svg>")
    return "".join(out)
