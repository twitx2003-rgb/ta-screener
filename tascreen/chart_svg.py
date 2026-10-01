"""A chart "screenshot" (SVG) of one detected pattern and its breakout.

The chart has one fixed look, like a real screenshot, in the night-violet palette: daily
candles around the pattern and plain volume bars. The annotations follow the
pattern-drawing skill (.claude/skills/pattern-drawing/SKILL.md; owner, 2026-10-01: "I
cannot see what you are talking about"): the pattern's own structure with its area lightly
shaded, a bold trigger line, a ringed dot on the breakout bar with the candle outlined, a
measure bracket that shows why the target is where it is, invalidation from the breakout
on, and the prices in tags on the right axis. At most five word tags in the price area,
each placed where it covers no candle, mark or other tag (or as little as possible).

What is drawn always comes from the detection's own geometry (turning points, lines,
breakout, target, trigger levels, `levels.invalidation`). A shape the record does not
carry (the cup's arc, a busted pattern's failing bar, where a line's first touch is) is
computed from the bars and the record's own dates in a small helper below. A caller
chooses which elements to draw (DRAWINGS) and may add a short caption (`note`).
Annotation elements carry class="ann".
"""
from __future__ import annotations

import html
import math
import re
from typing import Any

import numpy as np
import pandas as pd

from .indicators import sma
from .patterns.levels import REVERSALS, TRENDLINE_PATTERNS, geometry, invalidation

DRAWINGS = ("pivots", "pattern_lines", "confirm_line", "breakout", "target", "trigger",
            "failure", "volume", "sma", "zone")

W, H = 760, 440
LEFT, RIGHT, TOP, BOTTOM = 12, 74, 42, 26
VOLUME_SHARE = 0.17
# The window: the pattern takes at least half of it, after up to LEAD bars of the trend into
# it. MAX_BARS gives way only to keep the pattern's first bar (a long cup's left rim).
MAX_BARS, MIN_BARS, LEAD, MIN_LEAD = 170, 24, 25, 4
TAG_BUDGET = 5                       # word tags in the price area (skill section 2)
STRICT = 2                           # tags from this priority on never cover a candle
TAG_H = 22

INK = {"bg": "#130F20", "grid": "#221B36", "axis": "#8F86AE", "title": "#EDE9F8",
       "up": "#34D399", "down": "#FB7185", "sma50": "#FBBF24", "sma150": "#67E8F9"}
ANN = {"line": "#C4B5FD", "bull": "#34D399", "bear": "#FB7185", "target": "#FBBF24",
       "tag": "#1B1530", "note": "#221A3A", "note_ink": "#EDE9F8", "accent": "#8B5CF6"}
# Noto: what the analyst's runner has for Hebrew when librsvg turns a chart into a PNG
SANS = "Heebo, 'Segoe UI', 'Noto Sans Hebrew', 'Noto Sans', Arial, sans-serif"
MONO = "'IBM Plex Mono', Consolas, monospace"

FLAGS = frozenset({"flag", "pennant"})
# Bulkowski's wedge rule: the target is the wedge's own start, not a measured height
WEDGE_START = frozenset({("falling_wedge", "bullish"), ("rising_wedge", "bearish")})


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

    @property
    def box(self) -> tuple[float, float, float, float]:
        return self.x0, self.y0, self.x1, self.y1


# ----------------------------------------------------------------- geometry helpers
def _window(n: int, start_i: int, end_i: int) -> tuple[int, int]:
    """First and last bar shown. `end_i` is where the pattern ends (its breakout bar when
    it has one). The pattern gets at least half the width: the lead-in before it is at
    most as long as the pattern minus the bars after it (and a few for the space right
    of a fresh breakout). Only a breakout long ago leaves the pattern less than half."""
    last = n - 1
    span = max(1, end_i - start_i + 1)
    lead = max(MIN_LEAD, min(LEAD, span - (last - end_i) - 3))
    first = min(start_i - lead, last - MIN_BARS + 1)
    first = min(max(first, last - MAX_BARS + 1), start_i - MIN_LEAD)
    return max(0, first), last


class _Seg:
    """A record line in bar indices; `value` keeps the record's own equation."""

    def __init__(self, line: dict, i1: int, i2: int):
        self.label = str(line.get("label") or "")
        self.i1, self.i2 = i1, i2
        self.y1, self.y2 = float(line["y1"]), float(line["y2"])

    @property
    def flat(self) -> bool:
        return self.y1 == self.y2

    def value(self, i: float) -> float:
        if self.i2 == self.i1:
            return self.y1
        return self.y1 + (self.y2 - self.y1) * (i - self.i1) / (self.i2 - self.i1)


def _segments(lines: list[dict], index: dict[str, int]) -> list[_Seg]:
    out = []
    for ln in lines or []:
        i1, i2 = index.get(_iso(ln.get("x1")) or ""), index.get(_iso(ln.get("x2")) or "")
        if i1 is not None and i2 is not None and _ok(ln.get("y1")) and _ok(ln.get("y2")):
            out.append(_Seg(ln, i1, i2))
    return out


def _first_touch(seg: _Seg, segs: list[_Seg], points: list[tuple[int, float, str]]) -> int:
    """Where to start drawing a line: its first turning point (one close to it and no
    nearer to another line), never before the record's own start. A triangle's line
    fitted through its highs used to start at the first low and run off the chart."""
    if not points:
        return seg.i1
    prices = [p for _, p, _ in points]
    tol = 0.15 * (max(prices) - min(prices))

    def touches(i: int, price: float) -> bool:
        gap, eps = abs(seg.value(i) - price), 1e-4 * abs(price)
        return gap <= tol + eps and all(gap <= abs(s.value(i) - price) + eps for s in segs)

    found = [i for i, price, _ in points if seg.i1 <= i <= seg.i2 and touches(i, price)]
    return min(found) if found else seg.i1


def _clip(x1: float, y1: float, x2: float, y2: float,
          box: tuple[float, float, float, float]) -> tuple[float, float, float, float] | None:
    """The part of a segment inside the box (Liang-Barsky), or None."""
    left, top, right, bottom = box
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - left), (dx, right - x1), (-dy, y1 - top), (dy, bottom - y1)):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return x1 + t0 * dx, y1 + t0 * dy, x1 + t1 * dx, y1 + t1 * dy


def _cup_arc(lows: np.ndarray, anchors: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """A smooth arc through a cup's left rim, bottom and right rim (bar, price) that follows
    the bars' lows: the lows smoothed by a centred moving average, shifted (linearly
    between the anchors) so it passes exactly through each anchor, kept between the
    bottom and the higher rim."""
    (a, pa), (b, pb), (c, pc) = anchors
    k = max(3, (c - a) // 8) | 1
    smooth = pd.Series(lows[a:c + 1]).rolling(k, center=True, min_periods=1).mean().to_numpy()
    bars = np.arange(a, c + 1)
    shift = np.interp(bars, [a, b, c], [pa - smooth[0], pb - smooth[b - a], pc - smooth[-1]])
    arc = np.clip(smooth + shift, pb, max(pa, pc))
    return list(zip(bars.tolist(), arc.tolist()))


def _failure_bar(close: np.ndarray, brk_i: int, level: float, bullish: bool) -> int | None:
    """A busted pattern's failing session: the first close back beyond the invalidation
    level after the breakout (the detector's own "busted" test for the reversals)."""
    for i in range(brk_i + 1, len(close)):
        if (close[i] < level) if bullish else (close[i] > level):
            return i
    return None


def _measure(pattern: str, bullish: bool, points: list[tuple[int, float, str]], segs: list[_Seg],
             high: np.ndarray, low: np.ndarray, start_i: int, end_i: int, brk_i: int,
             length: float) -> tuple[float, float, float] | None:
    """The measure bracket inside the pattern: (bar, from price, to price). Its length is
    the record's breakout-to-target distance, placed where that height is measured (skill
    section 3), so the reader sees the same length twice."""
    if pattern in REVERSALS and points:
        if pattern.startswith("head_shoulders") and len(points) == 5:
            i, price, _ = points[2]                          # the head, down/up to the neckline
        else:
            i, price, _ = (min if bullish else max)(points[0::2], key=lambda p: p[1])
        return i, price, price + length
    if pattern == "rectangle":
        far = next((s for s in segs if ("תחתון" if bullish else "עליון") in s.label), None)
        if far is None:
            return None
        return end_i, far.value(brk_i), far.value(brk_i) + length
    if pattern in TRENDLINE_PATTERNS:                        # the widest part: the start
        base = float(low[start_i:end_i + 1].min()) if bullish else float(high[start_i:end_i + 1].max())
        return start_i - 1.2, base, base + length
    if pattern in FLAGS and len(points) == 2:                # along the pole, from whichever end
        (i, a, _), (_, b, _) = points                        # the length leads to the other
        base = a if abs(a + length - b) <= abs(b + length - a) else b
        return i - 1.2, base, base + length
    if pattern == "cup_with_handle" and len(points) == 3:   # rim down to the cup's bottom
        return points[1][0], points[1][1], points[1][1] + length
    return None


def _trigger(segs: list[_Seg], up: bool | None) -> _Seg | None:
    """The line a close has to cross: the confirmation line, else the neckline, else the
    side of a triangle / wedge / flag in the breakout's direction."""
    for word in ("אישור", "צוואר"):
        found = [s for s in segs if word in s.label]
        if found:
            return found[-1]
    if up is None:
        return None
    return next((s for s in segs if ("עליון" if up else "תחתון") in s.label), None)


# ------------------------------------------------------------------- tag placement
def _hits(a: tuple, b: tuple, pad: float = 0.0) -> bool:
    return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad


class _Tags:
    """Word tags in the price area: at most TAG_BUDGET, the most important first, each where
    it covers no other tag and as few candles, marks and lines as it can, joined to its
    anchor by a short leader when it had to move away."""

    def __init__(self, fr: _Frame):
        self.fr = fr
        self.candles: list[tuple[tuple, float]] = []   # (box, weight)
        self.marks: list[tuple] = []
        self.segments: list[tuple] = []
        self.boxes: list[tuple] = []
        self.wanted: list[tuple] = []

    def candle(self, x: float, y_hi: float, y_lo: float, weight: float) -> None:
        half = max(1.5, self.fr.step * 0.36)
        self.candles.append(((x - half, y_hi - 2, x + half, y_lo + 2), weight))

    def mark(self, x: float, y: float, r: float = 7.0) -> None:
        self.marks.append((x - r, y - r, x + r, y + r))

    def segment(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.segments.append((x1, y1, x2, y2))

    def want(self, priority: float, text: str, color: str, ax: float, ay: float, kind: str,
             side: int, slope: float = 0.0) -> None:
        """kind "point": around a mark at (ax, ay) (side -1 above, +1 below); "edge": at the
        right end of a line that starts at ax and is at height ay on the right edge (with
        `slope` in px per px), sliding left along it but never past its start (side -1
        above the line, +1 below). From priority STRICT on, a tag that would cover a candle
        is dropped instead."""
        self.wanted.append((priority, len(self.wanted), text, color, ax, ay, kind, side, slope))

    @staticmethod
    def width(text: str) -> float:
        return 18 + 7.2 * len(text)

    def _candidates(self, w: float, ax: float, ay: float, kind: str, side: int, slope: float):
        if kind == "edge":
            # farther than this, a level's word reads as another line's: leave the
            # price on the axis alone
            for dy in (15, 29):
                for s in (side, -side):
                    for k in range(16):
                        cx = self.fr.x1 - 4 - w / 2 - 30 * k
                        if k and cx - w / 2 < ax - 6:
                            break
                        # the line's height under the tag's nearer end
                        edge = cx + w / 2 if (slope < 0) == (s < 0) else cx - w / 2
                        yield cx, ay + slope * (edge - self.fr.x1) + s * dy
            return
        for dy in (20, 32, 46, 62, 80):
            for s in (side, -side):
                for dx in (0, -w / 2 - 4, 10 - w / 2, w / 2 + 4, -w, w):
                    yield ax + dx, ay + s * dy
        for s in (-1, 1):
            yield ax + s * (w / 2 + 12), ay

    def _cost(self, box: tuple, strict: bool) -> float | None:
        fr = self.fr
        if box[0] < fr.x0 + 2 or box[2] > fr.x1 - 2 or box[1] < fr.y0 + 1 or box[3] > fr.y1 - 1:
            return None
        if any(_hits(box, b, 3) for b in self.boxes):
            return None
        cost = sum(weight for b, weight in self.candles if _hits(box, b))
        if strict and cost:
            return None
        cost += 40 * sum(_hits(box, m, 1) for m in self.marks)
        cost += 5 * sum(_clip(*s, box) is not None for s in self.segments)
        return cost

    def render(self) -> list[str]:
        out = []
        for priority, _, text, color, ax, ay, kind, side, slope in sorted(self.wanted):
            if len(self.boxes) >= TAG_BUDGET:
                break
            w = self.width(text)
            best = None
            for cx, cy in self._candidates(w, ax, ay, kind, side, slope):
                box = (cx - w / 2, cy - TAG_H / 2, cx + w / 2, cy + TAG_H / 2)
                cost = self._cost(box, priority >= STRICT)
                if cost is None:
                    continue
                cost += math.hypot(cx - (self.fr.x1 if kind == "edge" else ax), cy - ay) / 25
                if kind == "point" and priority == 0:     # the breakout's tag leaves the space
                    cost += max(0.0, box[2] - ax + 4) / 6   # right of it to the level tags
                if best is None or cost < best[0]:
                    best = (cost, box)
            if best is None:
                continue                                  # no free spot: the less important goes
            box = best[1]
            self.boxes.append(box)
            near_x, near_y = min(max(ax, box[0]), box[2]), min(max(ay, box[1]), box[3])
            if kind == "point" and math.hypot(near_x - ax, near_y - ay) > 12:
                out.append(f'<line class="ann" x1="{ax:.1f}" y1="{ay:.1f}" x2="{near_x:.1f}" '
                           f'y2="{near_y:.1f}" stroke="{color}" stroke-width="1.2" stroke-opacity="0.6"/>')
            out.append(f'<g class="ann"><rect x="{box[0]:.1f}" y="{box[1]:.1f}" width="{w:.1f}" height="{TAG_H}" '
                       f'rx="6" fill="{ANN["tag"]}" stroke="{color}" stroke-width="1" opacity="0.95"/>'
                       f'<text x="{(box[0] + box[2]) / 2:.1f}" y="{box[1] + 15:.1f}" fill="{color}" '
                       f'font-family="{SANS}" font-size="13" font-weight="600" text-anchor="middle">'
                       f'{_esc(text)}</text></g>')
        return out


class _Axis:
    """Price tags on the right axis, coloured by role, pushed apart when they meet; a
    beak points at the true price. A tag may lead with a word ("חי" for the live price,
    right beside its dot)."""

    GAP = 21

    def __init__(self, fr: _Frame):
        self.fr = fr
        self.items: list[tuple[float, str, str]] = []

    def add(self, price: float, color: str, word: str = "") -> None:
        self.items.append((price, color, word))

    def layout(self) -> list[tuple[float, float, float, str, str]]:
        """(price, true y, tag y, colour, word), top to bottom."""
        fr = self.fr
        rows = sorted(([p, fr.y(p), fr.y(p), c, w] for p, c, w in self.items), key=lambda r: r[1])
        lo, hi = fr.y0 + 10, fr.y1 - 10
        for _ in range(3):
            for k, row in enumerate(rows):
                row[2] = min(max(row[2], lo, rows[k - 1][2] + self.GAP if k else lo), hi)
            for k in range(len(rows) - 2, -1, -1):
                rows[k][2] = min(rows[k][2], rows[k + 1][2] - self.GAP)
        return [tuple(r) for r in rows]

    def svg(self, rows) -> list[str]:
        fr, out = self.fr, []
        left, width = fr.x1 + 4, W - fr.x1 - 6
        for price, y_true, y_tag, color, word in rows:
            text = f"{word} {price:,.2f}".strip()
            size = min(13.0, (width - 6) / (0.62 * len(text)))
            out.append(f'<g class="ann axis"><path d="M{left:.1f},{y_tag - 6:.1f} L{fr.x1:.1f},{y_true:.1f} '
                       f'L{left:.1f},{y_tag + 6:.1f} Z" fill="{color}"/>'
                       f'<rect x="{left:.1f}" y="{y_tag - 10:.1f}" width="{width:.1f}" height="20" rx="4" '
                       f'fill="{color}"/><text x="{left + width / 2:.1f}" y="{y_tag + 4.5:.1f}" '
                       f'fill="{INK["bg"]}" font-family="{MONO}, {SANS}" font-size="{size:.1f}" font-weight="700" '
                       f'text-anchor="middle">{text}</text></g>')
        return out


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


# ------------------------------------------------------------------------- render
def render(bars: pd.DataFrame, det: dict[str, Any], drawings: list[str], note: str = "", *,
           seed: str, title: str, live_price: float | None = None) -> str:
    """The SVG markup for one chart post (`seed` names the chart)."""
    days = bars["timestamp"].dt.strftime("%Y-%m-%d").tolist()
    index = {d: i for i, d in enumerate(days)}
    high, low = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
    close = bars["close"].to_numpy(float)
    n = len(bars)
    last = n - 1
    wanted = {d for d in drawings if d in DRAWINGS}
    pattern, status = str(det.get("pattern") or ""), det.get("status")
    direction = det.get("direction")
    bullish = direction == "bullish"
    live = _ok(live_price)

    start_i = index.get(_iso(det.get("start")) or "", max(0, last - 60))
    end_i = index.get(_iso(det.get("end")) or "", last)
    brk_i = index.get(_iso(det.get("breakout_date")) or "")
    broken = brk_i is not None and status in ("breakout", "busted")
    bp = float(det["breakout_price"]) if _ok(det.get("breakout_price")) else math.nan
    points = [(index[_iso(p["date"])], float(p["price"]), str(p.get("label") or ""))
              for p in geometry(det, "points") if _iso(p.get("date")) in index and _ok(p.get("price"))]
    segs = _segments(geometry(det, "lines"), index)
    first, last = _window(n, start_i, brk_i if broken else end_i)
    win = bars.iloc[first:last + 1]

    # a live crossing: which side the price is beyond decides the trigger line
    triggers = {k: float(det[k]) for k in ("trigger_up", "trigger_down") if _ok(det.get(k))}
    up: bool | None = bullish if direction in ("bullish", "bearish") else None
    if live:
        crossed = [k for k, v in triggers.items() if (live_price >= v if k == "trigger_up" else live_price <= v)]
        if crossed:
            triggers = {crossed[0]: triggers[crossed[0]]}
            up = crossed[0] == "trigger_up"
    if not ("trigger" in wanted or live):
        triggers = {}
    trig = _trigger(segs, up) if wanted & {"confirm_line", "pattern_lines"} else None
    move = ANN["bull"] if up else (ANN["bear"] if up is False else ANN["line"])
    cancel_color = ANN["bear"] if bullish else ANN["bull"]

    target = float(det["target"]) if _ok(det.get("target")) else math.nan
    draw_target = "target" in wanted and broken and status != "busted" and _ok(target) and _ok(bp)
    wedge_start = (pattern, direction) in WEDGE_START
    cancel = invalidation(det, bars) if "failure" in wanted else math.nan
    failed_i = _failure_bar(close, brk_i, cancel, bullish) if status == "busted" and broken and _ok(cancel) else None

    levels = list(triggers.values())
    if draw_target:
        levels.append(target)
    if live:
        levels.append(float(live_price))
    if _ok(cancel):
        levels.append(cancel)
    hi = max([float(win["high"].max()), *levels])
    lo = min([float(win["low"].min()), *levels])
    span = (hi - lo) or 1.0
    # a level with a word tag near the top or bottom gets room for the tag beyond it
    near_hi = any(hi - v < 0.12 * span for v in levels)
    near_lo = any(v - lo < 0.12 * span for v in levels)
    pad_hi, pad_lo = span * (0.15 if near_hi else 0.08), span * (0.15 if near_lo else 0.08)
    # room right of a fresh breakout for the measure's projection and the level lines
    room = 0
    if broken and (draw_target or _ok(cancel)):
        step = (W - RIGHT - LEFT) / (len(win) + (1 if live else 0))
        room = max(0, math.ceil(30 / step) - (last - brk_i))
    fr = _Frame(first, len(win) + (1 if live else 0) + room, lo - pad_lo, hi + pad_hi)
    tags = _Tags(fr)
    axis = _Axis(fr)
    clip_id = "pp-" + (re.sub(r"[^A-Za-z0-9_-]", "", seed) or "chart")

    def at(i: int | None) -> int | None:
        return i if i is not None and fr.inside(i) else None

    under: list[str] = []          # shading, below the candles
    over: list[str] = []           # lines and marks, above them

    def line(x1, y1, x2, y2, color, width=2.0, dash="", opacity=1.0) -> None:
        seg = _clip(x1, y1, x2, y2, fr.box)
        if seg is None:
            return
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        extra += f' stroke-opacity="{opacity}"' if opacity < 1 else ""
        over.append(f'<line class="ann" x1="{seg[0]:.1f}" y1="{seg[1]:.1f}" x2="{seg[2]:.1f}" y2="{seg[3]:.1f}" '
                    f'stroke="{color}" stroke-width="{width}" stroke-linecap="round"{extra}/>')
        tags.segment(*seg)

    def dot(i: float, price: float, r: float = 4.5, color: str = ANN["line"]) -> None:
        x, y = fr.x(i), fr.y(price)
        over.append(f'<circle class="ann" cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{INK["bg"]}" '
                    f'stroke="{color}" stroke-width="2"/>')
        tags.mark(x, y, r + 2)

    def shade(pts: list[tuple[float, float]], opacity: float = 0.12, stroke: str = "") -> None:
        if len(pts) < 3:
            return
        d = " ".join(f"{fr.x(i):.1f},{fr.y(p):.1f}" for i, p in pts)
        edge = f' stroke="{stroke}" stroke-width="1.6"' if stroke else ""
        under.append(f'<polygon class="ann" points="{d}" fill="{ANN["line"]}" fill-opacity="{opacity}"'
                     f'{edge} clip-path="url(#{clip_id})"/>')

    def bracket(i: float, a: float, b: float, dash: str = "", arrow: bool = False) -> None:
        x, ya, yb = fr.x(i), fr.y(a), fr.y(b)
        halo = _clip(x, ya, x, yb, fr.box)
        if halo is not None:
            over.append(f'<line class="ann" x1="{halo[0]:.1f}" y1="{halo[1]:.1f}" x2="{halo[2]:.1f}" '
                        f'y2="{halo[3]:.1f}" stroke="{INK["bg"]}" stroke-width="5" stroke-opacity="0.7"/>')
        line(x, ya, x, yb, ANN["target"], 2.0, dash)
        line(x - 5, ya, x + 5, ya, ANN["target"], 2.0)
        if arrow:
            s = -1 if yb < ya else 1
            over.append(f'<path class="ann" d="M{x - 5:.1f},{yb - 8 * s:.1f} L{x:.1f},{yb:.1f} '
                        f'L{x + 5:.1f},{yb - 8 * s:.1f}" fill="none" stroke="{ANN["target"]}" '
                        f'stroke-width="2" stroke-linecap="round"/>')
        else:
            line(x - 5, yb, x + 5, yb, ANN["target"], 2.0)

    # ---------------------------------------------------------------- the pattern
    reversal_bottom = pattern.endswith("bottom")
    if "pattern_lines" in wanted:
        if pattern in REVERSALS and not pattern.startswith("head_shoulders") and len(points) >= 3 and trig:
            ext = [p[1] for p in points[0::2]]
            extreme = min(ext) if reversal_bottom else max(ext)
            level = trig.value(points[1][0])
            shade([(points[0][0], extreme), (points[-1][0], extreme),
                   (points[-1][0], level), (points[0][0], level)], 0.08)
        elif pattern in TRENDLINE_PATTERNS and len(segs) >= 2:
            top = next((s for s in segs if "עליון" in s.label), segs[0])
            bot = next((s for s in segs if "תחתון" in s.label), segs[1])
            t0, b0 = _first_touch(top, segs, points), _first_touch(bot, segs, points)
            e = min(top.i2, bot.i2)
            shade([(b0, bot.value(b0)), (e, bot.value(e)), (e, top.value(e)), (t0, top.value(t0))])
        elif pattern in FLAGS and len(segs) >= 2:
            a, b = segs[0], segs[1]
            shade([(a.i1, a.value(a.i1)), (a.i2, a.value(a.i2)), (b.i2, b.value(b.i2)), (b.i1, b.value(b.i1))])
        elif pattern == "high_tight_flag" and len(points) == 2 and end_i > points[1][0]:
            p = points[1][0]
            floor = float(low[p + 1:end_i + 1].min())
            shade([(p, points[1][1]), (end_i, points[1][1]), (end_i, floor), (p, floor)])
        elif pattern == "cup_with_handle" and len(points) == 3:
            arc = _cup_arc(low, [(i, price) for i, price, _ in points])
            shade(arc, 0.10)
            path = " ".join(f"{'M' if k == 0 else 'L'}{fr.x(i):.1f},{fr.y(p):.1f}" for k, (i, p) in enumerate(arc))
            over.append(f'<path class="ann" d="{path}" fill="none" stroke="{ANN["line"]}" stroke-width="2.4" '
                        f'stroke-linejoin="round" stroke-linecap="round" clip-path="url(#{clip_id})"/>')
            for (i1, p1), (i2, p2) in zip(arc[::6], arc[6::6]):
                tags.segment(fr.x(i1), fr.y(p1), fr.x(i2), fr.y(p2))
            r_i, r_p = points[2][0], points[2][1]
            if end_i > r_i:                                  # the handle: a light box under the rim
                floor = float(low[r_i + 1:end_i + 1].min())
                shade([(r_i, r_p), (end_i, r_p), (end_i, floor), (r_i, floor)], 0.14, ANN["line"])
        if (pattern in FLAGS or pattern == "high_tight_flag") and len(points) >= 2:
            (i1, p1, _), (i2, p2, _) = points[0], points[1]
            line(fr.x(i1), fr.y(p1), fr.x(i2), fr.y(p2), ANN["line"], 4.5)
            tags.want(3, "תורן", ANN["line"], fr.x((i1 + i2) / 2), fr.y((p1 + p2) / 2), "point",
                      1 if up is not False else -1)

    retest = None
    for seg in segs:
        is_trig = seg is trig
        if not (("pattern_lines" in wanted and not is_trig) or ("confirm_line" in wanted and is_trig)):
            continue
        a = max(_first_touch(seg, segs, points), first)
        b = min(seg.i2, last)
        if pattern.startswith("head_shoulders") and not is_trig:
            b = min(b, end_i)                                # the neckline as structure only
        if b < a:
            continue
        if is_trig and live and seg.i2 == last:
            b = last + 1                                     # on to the live session
        if is_trig:
            color = move if broken else ANN["line"]
            line(fr.x(a), fr.y(seg.value(a)), fr.x(b), fr.y(seg.value(b)), color, 2.8, "" if broken else "8 5")
            if broken and seg.flat and brk_i is not None and last > brk_i:
                retest = seg.y1
        else:
            line(fr.x(a), fr.y(seg.value(a)), fr.x(b), fr.y(seg.value(b)), ANN["line"], 2.0)
    if retest is not None:
        line(fr.x(brk_i), fr.y(retest), fr.x(last), fr.y(retest), move, 1.4, "4 5", 0.5)

    # forming / live: the trigger's price on the axis, and a horizontal line only when the
    # record has no line that reaches it (a fallback; the record's line is the real one)
    for key, value in triggers.items():
        if trig is None or abs(trig.value(last + 1) - value) > 0.002 * abs(value) or trig.i2 != last:
            line(fr.x(max(first, last - 25)), fr.y(value), fr.x(last + (1 if live else 0)), fr.y(value),
                 ANN["line"], 2.4, "8 5")
            from_x, slope = fr.x(max(first, last - 25)), 0.0
        else:
            from_x = fr.x(max(_first_touch(trig, segs, points), first))
            slope = (fr.y(trig.value(last + 1)) - fr.y(trig.value(last))) / fr.step
        axis.add(value, ANN["line"])
        tags.want(2, "פריצה מעל" if key == "trigger_up" else "פריצה מתחת", ANN["line"],
                  from_x, fr.y(value), "edge", -1 if key == "trigger_up" else 1, slope)

    if "pivots" in wanted:
        if pattern.startswith("head_shoulders") and len(points) == 5:
            for k, (i, price, _) in enumerate(points):
                if at(i) is None:
                    continue
                if k % 2:
                    dot(i, price, 3.0)
                else:
                    dot(i, price)
                    tags.want(3, "ראש" if k == 2 else "כתף", ANN["line"], fr.x(i), fr.y(price), "point",
                              1 if reversal_bottom else -1)
        elif pattern in REVERSALS:
            for k, (i, price, _) in enumerate(points):
                if at(i) is None:
                    continue
                if k % 2:
                    dot(i, price, 3.0)
                    continue
                dot(i, price)
                s = 1 if reversal_bottom else -1
                over.append(f'<text class="ann" x="{fr.x(i):.1f}" y="{fr.y(price) + s * 17 + 5:.1f}" '
                            f'fill="{ANN["line"]}" font-family="{MONO}" font-size="13" font-weight="700" '
                            f'text-anchor="middle">{k // 2 + 1}</text>')
                tags.mark(fr.x(i), fr.y(price) + s * 17, 8)
        elif pattern in TRENDLINE_PATTERNS:
            for i, price, _ in points:
                if at(i) is not None:
                    dot(i, price, 3.5)
        elif pattern == "cup_with_handle":
            for i, price, _ in points[0::2]:
                if at(i) is not None:
                    dot(i, price, 3.5)

    # ------------------------------------------------- target, invalidation, breakout
    right_x = fr.x1 - 2
    if draw_target and brk_i is not None:
        x_b = x_from = fr.x(max(brk_i, first))
        if wedge_start:                                     # the level comes from the wedge's start
            inside = slice(start_i, end_i + 1)
            start_bar = start_i + int(np.argmax(high[inside]) if bullish else np.argmin(low[inside]))
            x_from = fr.x(max(start_bar, first))
            line(x_from, fr.y(target), x_b, fr.y(target), ANN["target"], 1.4, "2 4", 0.7)
        line(x_b, fr.y(target), right_x, fr.y(target), ANN["target"], 1.8, "7 5")
        if not wedge_start:
            inside = _measure(pattern, bullish, points, segs, high, low, start_i, end_i, brk_i, target - bp)
            if inside is not None and fr.inside(int(math.floor(inside[0] + 0.5))):
                bracket(*inside)
            bracket(brk_i + max(0.9, 9 / fr.step), bp, target, "5 4", arrow=True)
        axis.add(target, ANN["target"])
        tags.want(2, "תחילת הטריז" if wedge_start else "יעד", ANN["target"], x_from, fr.y(target), "edge",
                  -1 if bullish else 1)
    if _ok(cancel):
        from_i = brk_i if broken else end_i
        line(fr.x(max(from_i, first)), fr.y(cancel), right_x, fr.y(cancel), cancel_color, 1.8, "2 5")
        axis.add(cancel, cancel_color)
        tags.want(4, "ביטול", cancel_color, fr.x(max(from_i, first)), fr.y(cancel), "edge", 1 if bullish else -1)
    if "breakout" in wanted and broken and at(brk_i) is not None and _ok(bp):
        x, half = fr.x(brk_i), max(3.0, fr.step * 0.31) + 2.5
        over.append(f'<rect class="ann" x="{x - half:.1f}" y="{fr.y(high[brk_i]) - 3:.1f}" width="{2 * half:.1f}" '
                    f'height="{fr.y(low[brk_i]) - fr.y(high[brk_i]) + 6:.1f}" rx="2.5" fill="none" '
                    f'stroke="{move}" stroke-width="1.6"/>')
        y = fr.y(bp)
        over.append(f'<circle class="ann" cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{INK["bg"]}" stroke="{move}" '
                    f'stroke-width="2.4"/><circle class="ann" cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{move}"/>')
        tags.mark(x, y, 9)
        tags.want(0, f"פריצה {pd.Timestamp(days[brk_i]):%d/%m}", move, x, y, "point",
                  1 if bullish else -1)
    if failed_i is not None and at(failed_i) is not None:
        x, y = fr.x(failed_i), fr.y(close[failed_i])
        over.append(f'<path class="ann" d="M{x - 6:.1f},{y - 6:.1f} L{x + 6:.1f},{y + 6:.1f} M{x - 6:.1f},{y + 6:.1f} '
                    f'L{x + 6:.1f},{y - 6:.1f}" stroke="{cancel_color}" stroke-width="2.6" stroke-linecap="round"/>')
        tags.mark(x, y, 8)
        tags.want(1, "כשל", cancel_color, x, y, "point", 1 if bullish else -1)
    if live:
        x, y = fr.x(last + 1), fr.y(float(live_price))
        over.append(f'<circle class="ann" cx="{x:.1f}" cy="{y:.1f}" r="9" fill="{ANN["bull"]}" fill-opacity="0.22"/>'
                    f'<circle class="ann" cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{ANN["bull"]}"/>')
        tags.mark(x, y, 9)
        axis.add(float(live_price), ANN["bull"], "חי")

    # ------------------------------------------------------------------- compose
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" class="shot-svg" role="img" '
           f'aria-label="{_esc(title)}" direction="ltr">',
           f'<defs><clipPath id="{clip_id}"><rect x="{fr.x0}" y="{fr.y0:.1f}" width="{fr.x1 - fr.x0}" '
           f'height="{fr.y1 - fr.y0:.1f}"/></clipPath></defs>',
           f'<rect width="{W}" height="{H}" rx="12" fill="{INK["bg"]}"/>']
    rows = axis.layout()
    for k in range(6):
        price = fr.lo + (fr.hi - fr.lo) * k / 5
        y = fr.y(price)
        out.append(f'<line x1="{fr.x0}" x2="{fr.x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{INK["grid"]}"/>')
        if all(abs(y - r[2]) > 16 for r in rows):          # an axis tag hides the grid's price
            out.append(f'<text x="{fr.x1 + 6}" y="{y + 4:.1f}" fill="{INK["axis"]}" font-family="{MONO}" '
                       f'font-size="12">{price:,.2f}</text>')
    for k in range(5):
        i = first + int(k * (len(win) - 1) / 4)
        anchor, x = ("start", fr.x0) if k == 0 else ("middle", fr.x(i))      # the first is never cut
        out.append(f'<text x="{x:.1f}" y="{H - 8}" fill="{INK["axis"]}" font-family="{MONO}" '
                   f'font-size="12" text-anchor="{anchor}">{pd.Timestamp(days[i]):%d/%m}</text>')
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
    out += under

    # volume and candles
    v_max = float(win["volume"].max()) or 1.0
    body = max(1.0, fr.step * 0.62)
    lead_in = brk_i if broken else end_i
    for i, row in zip(range(first, last + 1), win.itertuples(index=False)):
        color = INK["up"] if row.close >= row.open else INK["down"]
        x = fr.x(i)
        vh = (fr.v1 - fr.v0) * (row.volume / v_max if row.volume == row.volume else 0)
        out.append(f'<rect x="{x - body / 2:.1f}" y="{fr.v1 - vh:.1f}" width="{body:.1f}" '
                   f'height="{vh:.1f}" fill="{color}" opacity="0.3"/>')
        if "volume" in wanted and i == brk_i and broken:
            average = float(bars["volume"].iloc[max(0, i - 50):i].mean())
            if row.volume == row.volume and average == average and row.volume > average:
                out.append(f'<rect class="ann" x="{x - body / 2 - 1.5:.1f}" y="{fr.v1 - vh - 1.5:.1f}" '
                           f'width="{body + 3:.1f}" height="{vh + 1.5:.1f}" fill="none" stroke="{move}" '
                           f'stroke-width="1.4"/>')
        top, bot = fr.y(max(row.open, row.close)), fr.y(min(row.open, row.close))
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{fr.y(row.high):.1f}" y2="{fr.y(row.low):.1f}" '
                   f'stroke="{color}"/>')
        out.append(f'<rect x="{x - body / 2:.1f}" y="{top:.1f}" width="{body:.1f}" '
                   f'height="{max(1.0, bot - top):.1f}" fill="{color}"/>')
        tags.candle(x, fr.y(row.high), fr.y(row.low), 8 if start_i <= i <= lead_in else 3)
    if "sma" in wanted:
        for n_days, key in ((50, "sma50"), (150, "sma150")):
            avg = sma(bars["close"], n_days).iloc[first:last + 1]
            # only inside the price panel: each stretch inside is its own line
            runs: list[list[str]] = [[]]
            for i, v in zip(range(first, last + 1), avg):
                if v == v and fr.lo <= v <= fr.hi:
                    runs[-1].append(f"{fr.x(i):.1f},{fr.y(v):.1f}")
                elif runs[-1]:
                    runs.append([])
            for pts in runs:
                if len(pts) > 1:
                    out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{INK[key]}" '
                               f'stroke-width="1.4" stroke-opacity="0.85"/>')
            value = avg.iloc[-1]
            if value == value:
                tags.want(6, f"SMA{n_days}", INK[key], fr.x(last), fr.y(value), "point", -1)

    out += over
    out += tags.render()
    out += axis.svg(rows)

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
