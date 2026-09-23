"""A chart "screenshot" (SVG) with marker-style drawings on it.

The chart looks the same in either site theme, like a real screenshot: daily
candles around the pattern, volume, SMA50 and SMA150. The drawings imitate a
highlighter: slightly shaky strokes (the shake is seeded by the post id, so a
chart never changes between page loads), circles, arrows and handwritten labels.

What is drawn always comes from the detection's own geometry (turning points,
lines, breakout, target, trigger levels); an agent only chooses which of these
to draw (DRAWINGS) and may add a short caption (`note`).
"""
from __future__ import annotations

import html
import math
import random
from typing import Any

import pandas as pd

from ..indicators import sma

DRAWINGS = ("pivots", "pattern_lines", "confirm_line", "breakout", "target", "trigger",
            "failure", "volume", "sma", "zone")

W, H = 760, 440
LEFT, RIGHT, TOP, BOTTOM = 12, 66, 40, 26
VOLUME_SHARE = 0.17
MAX_BARS, MIN_BARS, LEAD = 170, 70, 25

INK = {"bg": "#121916", "grid": "#1F2925", "axis": "#8E9C95", "title": "#E3EBE7",
       "up": "#4DC48B", "down": "#FF7D70", "sma50": "#E9A94A", "sma150": "#B49BEB"}
MARKER = {"main": "#FFD84D", "bull": "#7CF29A", "bear": "#FF6FA8", "note": "#FFF3B0",
          "note_ink": "#1B1B1B"}
HAND = "'Amatic SC', 'Segoe Print', 'Comic Sans MS', cursive"
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


class _Pen:
    """Highlighter strokes with a small, seeded shake."""

    def __init__(self, seed: str):
        self.rng = random.Random(seed)
        self.out: list[str] = []

    def _shake(self, pts: list[tuple[float, float]], amount: float) -> list[tuple[float, float]]:
        dense = []
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            steps = max(2, int(math.hypot(xb - xa, yb - ya) / 22))
            for k in range(steps):
                t = k / steps
                dense.append((xa + (xb - xa) * t, ya + (yb - ya) * t))
        dense.append(pts[-1])
        return [(x + self.rng.uniform(-amount, amount), y + self.rng.uniform(-amount, amount))
                for x, y in dense]

    def stroke(self, pts: list[tuple[float, float]], color: str, width: float = 3.2,
               dash: str = "") -> None:
        for alpha, shake in ((0.95, 1.1), (0.35, 2.0)):          # a second, fainter pass
            path = self._shake(pts, shake)
            d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in path)
            extra = f' stroke-dasharray="{dash}"' if dash else ""
            self.out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
                            f'stroke-linecap="round" stroke-linejoin="round" opacity="{alpha}"{extra}/>')

    def ring(self, cx: float, cy: float, rx: float, ry: float, color: str) -> None:
        start = self.rng.uniform(0, 2 * math.pi)
        pts = [(cx + rx * math.cos(start + a / 16 * 2.15 * math.pi),
                cy + ry * math.sin(start + a / 16 * 2.15 * math.pi)) for a in range(17)]
        self.stroke(pts, color, 2.6)

    def arrow(self, x: float, y: float, up: bool, color: str) -> None:
        tail = 44 if up else -44
        self.stroke([(x - 14, y + tail), (x - 4, y + tail * 0.45), (x, y + (8 if up else -8))], color)
        tip = y + (8 if up else -8)
        head = 9 if up else -9
        self.stroke([(x - 8, tip + head), (x, tip), (x + 8, tip + head)], color)

    def label(self, x: float, y: float, text: str, color: str, size: int = 19,
              anchor: str = "middle") -> None:
        self.out.append(f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-family="{HAND}" '
                        f'font-size="{size}" font-weight="700" text-anchor="{anchor}" '
                        f'paint-order="stroke" stroke="{INK["bg"]}" stroke-width="4">{_esc(text)}</text>')


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


def _window(bars: pd.DataFrame, start_i: int) -> tuple[int, int]:
    last = len(bars) - 1
    first = max(0, min(start_i - LEAD, last - MIN_BARS + 1))
    first = max(first, last - MAX_BARS + 1)
    return first, last


def render(bars: pd.DataFrame, det: dict[str, Any], drawings: list[str], note: str = "", *,
           seed: str, title: str, live_price: float | None = None) -> str:
    """The SVG markup for one chart post."""
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
    pad = (hi - lo) * 0.07 or 1.0
    fr = _Frame(first, len(win), lo - pad, hi + pad)
    pen = _Pen(seed)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" class="shot" role="img" '
           f'aria-label="{_esc(title)}" direction="ltr">',
           f'<rect width="{W}" height="{H}" rx="10" fill="{INK["bg"]}"/>']

    # grid and price scale
    for k in range(6):
        price = fr.lo + (fr.hi - fr.lo) * k / 5
        y = fr.y(price)
        out.append(f'<line x1="{fr.x0}" x2="{fr.x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{INK["grid"]}"/>')
        out.append(f'<text x="{fr.x1 + 6}" y="{y + 4:.1f}" fill="{INK["axis"]}" font-family="{MONO}" '
                   f'font-size="11">{price:,.2f}</text>')
    for k in range(5):
        i = first + int(k * (len(win) - 1) / 4)
        stamp = pd.Timestamp(days[i])
        out.append(f'<text x="{fr.x(i):.1f}" y="{H - 8}" fill="{INK["axis"]}" font-family="{MONO}" '
                   f'font-size="11" text-anchor="middle">{stamp:%d/%m}</text>')
    out.append(f'<text x="{fr.x0 + 4}" y="24" fill="{INK["title"]}" font-family="{MONO}" '
               f'font-size="14" font-weight="600">{_esc(title)}</text>')
    out.append(f'<text x="{fr.x1}" y="24" fill="{INK["axis"]}" font-family="{MONO}" font-size="12" '
               f'text-anchor="end">1D · {pd.Timestamp(days[last]):%d/%m/%Y}</text>')

    if "zone" in wanted:
        p_lo = float(bars["low"].iloc[start_i:end_i + 1].min())
        p_hi = float(bars["high"].iloc[start_i:end_i + 1].max())
        x_a, x_b = fr.x(max(start_i, first)) - fr.step / 2, fr.x(end_i) + fr.step / 2
        out.append(f'<rect x="{x_a:.1f}" y="{fr.y(p_hi):.1f}" width="{x_b - x_a:.1f}" '
                   f'height="{fr.y(p_lo) - fr.y(p_hi):.1f}" fill="{MARKER["main"]}" opacity="0.10"/>')

    # volume, candles, averages
    v_max = float(win["volume"].max()) or 1.0
    body = max(1.0, fr.step * 0.62)
    for i, row in zip(range(first, last + 1), win.itertuples(index=False)):
        color = INK["up"] if row.close >= row.open else INK["down"]
        x = fr.x(i)
        vh = (fr.v1 - fr.v0) * (row.volume / v_max if row.volume == row.volume else 0)
        out.append(f'<rect x="{x - body / 2:.1f}" y="{fr.v1 - vh:.1f}" width="{body:.1f}" '
                   f'height="{vh:.1f}" fill="{color}" opacity="0.35"/>')
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
                       f'stroke-width="1.4"/>')

    # ---------------------------------------------------------------- drawings
    color = MARKER["bull"] if bullish else (MARKER["bear"] if det.get("direction") == "bearish"
                                            else MARKER["main"])

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
            pen.stroke([(fr.x(i1), fr.y(ln["y1"])), (fr.x(i2), fr.y(ln["y2"]))], MARKER["main"])
            if confirm and ln.get("label"):
                pen.label(fr.x(i2) - 30, fr.y(ln["y2"]) - 10, ln["label"], MARKER["main"], 17)
    if "pivots" in wanted:
        for p in det.get("points") or []:
            i = at(p.get("date"))
            if i is None or not _ok(p.get("price")):
                continue
            above = p["price"] >= (bars["high"].iloc[i] + bars["low"].iloc[i]) / 2
            pen.ring(fr.x(i), fr.y(p["price"]), 12, 10, MARKER["main"])
            if p.get("label"):
                pen.label(fr.x(i), fr.y(p["price"]) + (-18 if above else 30), p["label"], MARKER["main"], 18)
    if "breakout" in wanted:
        i = at(det.get("breakout_date"))
        if i is not None:
            up = bullish
            y = fr.y(bars["low"].iloc[i] if up else bars["high"].iloc[i])
            pen.arrow(fr.x(i), y, up, color)
            pen.label(fr.x(i) - 16, y + (62 if up else -52), "פריצה", color, 20)
    right_x = fr.x1 - 4
    if "target" in wanted and _ok(det.get("target")):
        from_i = at(det.get("breakout_date")) or min(end_i, last)
        y = fr.y(float(det["target"]))
        pen.stroke([(fr.x(from_i), y), (right_x, y)], color, 2.6, "10 8")
        pen.label(right_x - 70, y - 9, "יעד (כלל המדידה)", color, 18)
    if "trigger" in wanted:
        for key, text in (("trigger_up", "פריצה מעל"), ("trigger_down", "פריצה מתחת")):
            if _ok(det.get(key)):
                y = fr.y(float(det[key]))
                pen.stroke([(fr.x(max(first, last - 25)), y), (right_x, y)], MARKER["main"], 2.6, "6 6")
                pen.label(right_x, y + (-9 if key == "trigger_up" else 22),
                          f"{text} {float(det[key]):,.2f}", MARKER["main"], 18, "end")
    if "failure" in wanted and det.get("direction") in ("bullish", "bearish"):
        seg = bars.iloc[start_i:end_i + 1]
        level = float(seg["low"].min()) if bullish else float(seg["high"].max())
        y = fr.y(level)
        pen.stroke([(fr.x(max(start_i, first)), y), (right_x, y)], MARKER["bear"] if bullish else MARKER["bull"],
                   2.2, "3 7")
        pen.label(right_x - 40, y + (22 if bullish else -9), "ביטול", MARKER["bear"] if bullish else MARKER["bull"], 18)
    if "volume" in wanted:
        x_a, x_b = fr.x(max(start_i, first)) - 6, fr.x(min(end_i, last)) + 6
        pen.stroke([(x_a, fr.v0 - 4), (x_b, fr.v0 - 4), (x_b, fr.v1 + 2), (x_a, fr.v1 + 2), (x_a, fr.v0 - 4)],
                   MARKER["main"], 2.2)
        pen.label(x_b - 8, fr.v0 + 16, "נפח", MARKER["main"], 17, "end")
    if "sma" in wanted:
        for n, key in ((50, "sma50"), (150, "sma150")):
            value = sma(bars["close"], n).iloc[last]
            if value == value:
                pen.label(fr.x(last) - 8, fr.y(value) - 8, f"SMA{n}", INK[key], 17, "end")
    if _ok(live_price):
        y = fr.y(float(live_price))
        out.append(f'<circle cx="{fr.x(last) + fr.step:.1f}" cy="{y:.1f}" r="4" fill="{MARKER["bull"]}"/>')
        pen.label(fr.x(last) - 10, y - 8, "חי", MARKER["bull"], 17, "end")
    out += pen.out

    caption = " ".join(str(note or "").split())[:60]
    if caption:
        width = min(300, 16 + 9.5 * len(caption))
        x, y = _emptiest_corner(win, fr, width, 36)
        tilt = random.Random(seed + "note").uniform(-2.5, 2.5)
        out.append(f'<g transform="rotate({tilt:.1f} {x + width / 2:.1f} {y + 18:.1f})">'
                   f'<rect x="{x}" y="{y}" width="{width:.0f}" height="36" rx="6" fill="{MARKER["note"]}" '
                   f'opacity="0.95"/><text x="{x + width / 2:.0f}" y="{y + 25}" fill="{MARKER["note_ink"]}" '
                   f'font-family="{HAND}" font-size="21" font-weight="700" text-anchor="middle">'
                   f'{_esc(caption)}</text></g>')
    out.append("</svg>")
    return "".join(out)
