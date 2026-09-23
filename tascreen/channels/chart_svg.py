"""A chart "screenshot" (SVG) with clean annotations on it.

The chart has one fixed look, like a real screenshot, in the site's night-violet
palette: daily candles around the pattern, volume, SMA50 and SMA150. The
annotations are the precise kind a charting tool draws (owner's choice,
2026-09-24): straight lines, ringed markers, arrows, and labels in small tags.

What is drawn always comes from the detection's own geometry (turning points,
lines, breakout, target, trigger levels); an agent only chooses which of these
to draw (DRAWINGS) and may add a short caption (`note`). Annotation elements
carry class="ann".
"""
from __future__ import annotations

import html
import math
from typing import Any

import pandas as pd

from ..indicators import sma

DRAWINGS = ("pivots", "pattern_lines", "confirm_line", "breakout", "target", "trigger",
            "failure", "volume", "sma", "zone")

W, H = 760, 440
LEFT, RIGHT, TOP, BOTTOM = 12, 66, 42, 26
VOLUME_SHARE = 0.17
MAX_BARS, MIN_BARS, LEAD = 170, 70, 25

INK = {"bg": "#130F20", "grid": "#221B36", "axis": "#8F86AE", "title": "#EDE9F8",
       "up": "#34D399", "down": "#FB7185", "sma50": "#FBBF24", "sma150": "#67E8F9"}
ANN = {"line": "#C4B5FD", "bull": "#34D399", "bear": "#FB7185", "target": "#FBBF24",
       "tag": "#1B1530", "note": "#221A3A", "note_ink": "#EDE9F8", "accent": "#8B5CF6"}
SANS = "Heebo, 'Segoe UI', Arial, sans-serif"
MONO = "'IBM Plex Mono', Consolas, monospace"


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _iso(value: Any) -> str | None:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return pd.Timestamp(value).date().isoformat() if not isinstance(value, str) else value[:10]


def _ok(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


class _Frame:
    """Maps bar indices and prices to SVG coordinates."""

    def __init__(self, first: int, count: int, lo: float, hi: float):
        self.first, self.count, self.lo, self.hi = first, count, lo, hi
        self.x0, self.x1 = LEFT, W - RIGHT
        plot = H - TOP - BOTTOM
        self.y0, self.y1 = TOP, TOP + plot * (1 - VOLUME_SHARE) - 6
        self.v0, self.v1 = self.y1 + 10, H - BOTTOM
        self.step = (self.x1 - self.x0) / count

    def x(self, i: float) -> float:
        return self.x0 + (i - self.first + 0.5) * self.step

    def y(self, price: float) -> float:
        return self.y0 + (self.hi - price) / (self.hi - self.lo) * (self.y1 - self.y0)

    def inside(self, i: int) -> bool:
        return self.first <= i < self.first + self.count


class _Annotations:
    """Precise annotation shapes, all tagged class="ann"."""

    def __init__(self):
        self.out: list[str] = []
        self.boxes: list[tuple[float, float, float, float]] = []  # placed tags: left, top, right, bottom

    def line(self, x1: float, y1: float, x2: float, y2: float, color: str, width: float = 2.0,
             dash: str = "") -> None:
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.out.append(f'<line class="ann" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                        f'stroke="{color}" stroke-width="{width}" stroke-linecap="round"{extra}/>')

    def marker(self, x: float, y: float, color: str) -> None:
        self.out.append(f'<circle class="ann" cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="{INK["bg"]}" '
                        f'stroke="{color}" stroke-width="2"/>')

    def arrow(self, x: float, y: float, up: bool, color: str) -> None:
        """An arrow pointing at (x, y): from below when `up`, from above otherwise."""
        sign = 1 if up else -1
        tip = y + 6 * sign
        self.out.append(f'<path class="ann" d="M{x:.1f},{tip + 34 * sign:.1f} L{x:.1f},{tip + 9 * sign:.1f}" '
                        f'stroke="{color}" stroke-width="2.4" stroke-linecap="round" fill="none"/>')
        self.out.append(f'<path class="ann" d="M{x - 6:.1f},{tip + 10 * sign:.1f} L{x:.1f},{tip:.1f} '
                        f'L{x + 6:.1f},{tip + 10 * sign:.1f} Z" fill="{color}"/>')

    def tag(self, x: float, y: float, text: str, color: str, anchor: str = "middle") -> None:
        """A label in a small rounded tag; (x, y) is its anchor point, y its middle."""
        width = 18 + 7.2 * len(text)
        left = {"middle": x - width / 2, "end": x - width, "start": x}[anchor]
        left = min(max(left, LEFT + 2), W - RIGHT - width - 2)
        y = self._free_y(left, left + width, y)
        self.boxes.append((left, y - 11, left + width, y + 11))
        self.out.append(f'<g class="ann"><rect x="{left:.1f}" y="{y - 11:.1f}" width="{width:.1f}" height="22" '
                        f'rx="6" fill="{ANN["tag"]}" stroke="{color}" stroke-width="1" opacity="0.95"/>'
                        f'<text x="{left + width / 2:.1f}" y="{y + 4:.1f}" fill="{color}" font-family="{SANS}" '
                        f'font-size="13" font-weight="600" text-anchor="middle">{_esc(text)}</text></g>')


    def _free_y(self, left: float, right: float, y: float) -> float:
        """The nearest height to `y` where a tag spanning left..right covers no earlier tag."""
        def clear(cy: float) -> bool:
            return all(right + 3 <= b[0] or left - 3 >= b[2] or cy + 14 <= b[1] or cy - 14 >= b[3]
                       for b in self.boxes)

        lowest, highest = TOP + 12, H - BOTTOM - 12
        for shift in (0, 26, -26, 52, -52, 78, -78):
            cy = min(max(y + shift, lowest), highest)
            if clear(cy):
                return cy
        return min(max(y, lowest), highest)


def _window(bars: pd.DataFrame, start_i: int) -> tuple[int, int]:
    last = len(bars) - 1
    first = max(0, min(start_i - LEAD, last - MIN_BARS + 1))
    first = max(first, last - MAX_BARS + 1)
    return first, last


def _emptiest_corner(win: pd.DataFrame, fr: _Frame, width: float, height: float) -> tuple[float, float]:
    """Top-left corner for the caption: whichever corner of the price area has the
    fewest candles crossing the caption's box."""
    best, best_hits = (fr.x0 + 12, fr.y0 + 10), None
    for left in (True, False):
        for top in (True, False):
            x = fr.x0 + 12 if left else fr.x1 - 12 - width
            y = fr.y0 + 10 if top else fr.y1 - 10 - height
            hits = 0
            for i, row in zip(range(fr.first, fr.first + fr.count), win.itertuples(index=False)):
                if x - 6 <= fr.x(i) <= x + width + 6 and fr.y(row.high) <= y + height + 6 \
                        and fr.y(row.low) >= y - 6:
                    hits += 1
            if best_hits is None or hits < best_hits:
                best, best_hits = (x, y), hits
    return best


def render(bars: pd.DataFrame, det: dict[str, Any], drawings: list[str], note: str = "", *,
           seed: str, title: str, live_price: float | None = None) -> str:
    """The SVG markup for one chart post (`seed` names the chart; kept for callers)."""
    days = bars["timestamp"].dt.strftime("%Y-%m-%d").tolist()
    index = {d: i for i, d in enumerate(days)}
    last = len(bars) - 1
    start_i = index.get(_iso(det.get("start")) or "", max(0, last - 60))
    end_i = index.get(_iso(det.get("end")) or "", last)
    first, last = _window(bars, start_i)
    win = bars.iloc[first:last + 1]
    bullish = det.get("direction") == "bullish"
    wanted = [d for d in drawings if d in DRAWINGS]

    levels = []
    if "target" in wanted and _ok(det.get("target")):
        levels.append(float(det["target"]))
    if "trigger" in wanted:
        levels += [float(det[k]) for k in ("trigger_up", "trigger_down") if _ok(det.get(k))]
    if _ok(live_price):
        levels.append(float(live_price))
    hi = max([float(win["high"].max()), *levels])
    lo = min([float(win["low"].min()), *levels])
    pad = (hi - lo) * 0.08 or 1.0
    fr = _Frame(first, len(win), lo - pad, hi + pad)
    ann = _Annotations()
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" class="shot-svg" role="img" '
           f'aria-label="{_esc(title)}" direction="ltr">',
           f'<rect width="{W}" height="{H}" rx="12" fill="{INK["bg"]}"/>']

    # grid and scales
    for k in range(6):
        price = fr.lo + (fr.hi - fr.lo) * k / 5
        y = fr.y(price)
        out.append(f'<line x1="{fr.x0}" x2="{fr.x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{INK["grid"]}"/>')
        out.append(f'<text x="{fr.x1 + 6}" y="{y + 4:.1f}" fill="{INK["axis"]}" font-family="{MONO}" '
                   f'font-size="12">{price:,.2f}</text>')
    for k in range(5):
        i = first + int(k * (len(win) - 1) / 4)
        out.append(f'<text x="{fr.x(i):.1f}" y="{H - 8}" fill="{INK["axis"]}" font-family="{MONO}" '
                   f'font-size="12" text-anchor="middle">{pd.Timestamp(days[i]):%d/%m}</text>')
    out.append(f'<text x="{fr.x0 + 4}" y="26" fill="{INK["title"]}" font-family="{MONO}" '
               f'font-size="14" font-weight="600">{_esc(title)}</text>')
    out.append(f'<text x="{fr.x1}" y="26" fill="{INK["axis"]}" font-family="{MONO}" font-size="12" '
               f'text-anchor="end">1D · {pd.Timestamp(days[last]):%d/%m/%Y}</text>')

    if "zone" in wanted:
        p_lo = float(bars["low"].iloc[start_i:end_i + 1].min())
        p_hi = float(bars["high"].iloc[start_i:end_i + 1].max())
        x_a, x_b = fr.x(max(start_i, first)) - fr.step / 2, fr.x(end_i) + fr.step / 2
        out.append(f'<rect class="ann" x="{x_a:.1f}" y="{fr.y(p_hi):.1f}" width="{x_b - x_a:.1f}" '
                   f'height="{fr.y(p_lo) - fr.y(p_hi):.1f}" fill="{ANN["accent"]}" fill-opacity="0.10" '
                   f'stroke="{ANN["line"]}" stroke-opacity="0.45" stroke-dasharray="4 4"/>')

    # volume, candles, averages
    v_max = float(win["volume"].max()) or 1.0
    body = max(1.0, fr.step * 0.62)
    for i, row in zip(range(first, last + 1), win.itertuples(index=False)):
        color = INK["up"] if row.close >= row.open else INK["down"]
        x = fr.x(i)
        vh = (fr.v1 - fr.v0) * (row.volume / v_max if row.volume == row.volume else 0)
        out.append(f'<rect x="{x - body / 2:.1f}" y="{fr.v1 - vh:.1f}" width="{body:.1f}" '
                   f'height="{vh:.1f}" fill="{color}" opacity="0.3"/>')
        top, bot = fr.y(max(row.open, row.close)), fr.y(min(row.open, row.close))
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{fr.y(row.high):.1f}" y2="{fr.y(row.low):.1f}" '
                   f'stroke="{color}"/>')
        out.append(f'<rect x="{x - body / 2:.1f}" y="{top:.1f}" width="{body:.1f}" '
                   f'height="{max(1.0, bot - top):.1f}" fill="{color}"/>')
    for n, key in ((50, "sma50"), (150, "sma150")):
        line = sma(bars["close"], n).iloc[first:last + 1]
        pts = [f"{fr.x(i):.1f},{fr.y(v):.1f}" for i, v in zip(range(first, last + 1), line) if v == v]
        if len(pts) > 1:
            out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{INK[key]}" '
                       f'stroke-width="1.4" stroke-opacity="0.85"/>')

    # ------------------------------------------------------------- annotations
    color = ANN["bull"] if bullish else (ANN["bear"] if det.get("direction") == "bearish" else ANN["line"])

    def at(day: Any) -> int | None:
        i = index.get(_iso(day) or "")
        return i if i is not None and fr.inside(i) else None

    if "pattern_lines" in wanted or "confirm_line" in wanted:
        for ln in det.get("lines") or []:
            confirm = any(w in ln.get("label", "") for w in ("אישור", "צוואר"))
            if "pattern_lines" not in wanted and not confirm:
                continue
            i1, i2 = at(ln.get("x1")), at(ln.get("x2"))
            if i1 is None or i2 is None or not (_ok(ln.get("y1")) and _ok(ln.get("y2"))):
                continue
            ann.line(fr.x(i1), fr.y(ln["y1"]), fr.x(i2), fr.y(ln["y2"]), ANN["line"], 2.2)
            if confirm and ln.get("label"):
                ann.tag(fr.x(i2), fr.y(ln["y2"]) + 18, ln["label"], ANN["line"], "end")
    if "pivots" in wanted:
        for p in det.get("points") or []:
            i = at(p.get("date"))
            if i is None or not _ok(p.get("price")):
                continue
            above = p["price"] >= (bars["high"].iloc[i] + bars["low"].iloc[i]) / 2
            ann.marker(fr.x(i), fr.y(p["price"]), ANN["line"])
            if p.get("label"):
                ann.tag(fr.x(i), fr.y(p["price"]) + (-20 if above else 20), p["label"], ANN["line"])
    if "breakout" in wanted:
        i = at(det.get("breakout_date"))
        if i is not None:
            y = fr.y(bars["low"].iloc[i] if bullish else bars["high"].iloc[i])
            ann.arrow(fr.x(i), y, bullish, color)
            ann.tag(fr.x(i), y + (58 if bullish else -58), "פריצה", color)
    right_x = fr.x1 - 4
    if "target" in wanted and _ok(det.get("target")):
        from_i = at(det.get("breakout_date")) or min(end_i, last)
        y = fr.y(float(det["target"]))
        ann.line(fr.x(from_i), y, right_x, y, ANN["target"], 1.6, "7 5")
        ann.tag(right_x, y - 14, f"יעד (כלל המדידה) {float(det['target']):,.2f}", ANN["target"], "end")
    if "trigger" in wanted:
        for key, text in (("trigger_up", "פריצה מעל"), ("trigger_down", "פריצה מתחת")):
            if _ok(det.get(key)):
                y = fr.y(float(det[key]))
                ann.line(fr.x(max(first, last - 25)), y, right_x, y, color, 1.6, "5 4")
                ann.tag(right_x, y + (-14 if key == "trigger_up" else 14),
                        f"{text} {float(det[key]):,.2f}", color, "end")
    if "failure" in wanted and det.get("direction") in ("bullish", "bearish"):
        seg = bars.iloc[start_i:end_i + 1]
        level = float(seg["low"].min()) if bullish else float(seg["high"].max())
        y = fr.y(level)
        cancel = ANN["bear"] if bullish else ANN["bull"]
        ann.line(fr.x(max(start_i, first)), y, right_x, y, cancel, 1.4, "2 5")
        ann.tag(right_x, y + (14 if bullish else -14), f"ביטול {level:,.2f}", cancel, "end")
    if "volume" in wanted:
        x_a, x_b = fr.x(max(start_i, first)) - 5, fr.x(min(end_i, last)) + 5
        out.append(f'<rect class="ann" x="{x_a:.1f}" y="{fr.v0 - 4:.1f}" width="{x_b - x_a:.1f}" '
                   f'height="{fr.v1 - fr.v0 + 6:.1f}" rx="4" fill="none" stroke="{ANN["line"]}" '
                   f'stroke-width="1.2" stroke-dasharray="4 3"/>')
        ann.tag(x_b, fr.v0 - 4, "נפח", ANN["line"], "end")
    if "sma" in wanted:
        for n, key in ((50, "sma50"), (150, "sma150")):
            value = sma(bars["close"], n).iloc[last]
            if value == value:
                ann.tag(fr.x(last) - 6, fr.y(value) - 14, f"SMA{n}", INK[key], "end")
    if _ok(live_price):
        y = fr.y(float(live_price))
        out.append(f'<circle class="ann" cx="{fr.x(last) + fr.step:.1f}" cy="{y:.1f}" r="4" fill="{ANN["bull"]}"/>')
        ann.tag(fr.x(last) - 10, y - 14, f"חי {float(live_price):,.2f}", ANN["bull"], "end")
    out += ann.out

    caption = " ".join(str(note or "").split())[:60]
    if caption:
        width = min(360, 28 + 8.2 * len(caption))
        x, y = _emptiest_corner(win, fr, width, 34)
        out.append(f'<g class="ann"><rect x="{x:.1f}" y="{y:.1f}" width="{width:.0f}" height="34" rx="9" '
                   f'fill="{ANN["note"]}" stroke="{ANN["accent"]}" stroke-opacity="0.6"/>'
                   f'<rect x="{x + width - 4:.1f}" y="{y + 7:.1f}" width="3" height="20" rx="1.5" '
                   f'fill="{ANN["accent"]}"/>'
                   f'<text x="{x + (width - 6) / 2:.0f}" y="{y + 22:.1f}" fill="{ANN["note_ink"]}" '
                   f'font-family="{SANS}" font-size="15" font-weight="600" text-anchor="middle">'
                   f'{_esc(caption)}</text></g>')
    out.append("</svg>")
    return "".join(out)
