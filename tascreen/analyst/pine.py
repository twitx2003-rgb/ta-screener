"""The analysis as a TradingView Pine Script (v6): pasted once into the Pine Editor, it
draws the simple view (view.py) on the owner's own chart: the nearest support and
resistance zones, and the Fibonacci retracements and the volume profile when they matter
now. Each part is a switch in the indicator's settings; Fibonacci and the profile start
on only when they matter. (TradingView's MCP server has no drawing tools, so this is the
way onto the owner's charts.)

How it anchors: a zone starts at its first touch and the Fibonacci lines at the end of
the swing; the script finds those session dates by year/month/day on the chart's own
bars (exchange time) and draws with xloc.bar_index, so no time zone or bar time has to be
guessed. It checks that it runs on the right symbol, on a daily chart, and that the
chart's closes on the anchor dates match the analysis's (within 0.5%; a chart adjusted
for dividends, or another feed, shows a warning).

Only data is generated: numbers formatted from floats and text through `_pine_string`
(escaped, one line), into a fixed layout. Everything is drawn once, on the last bar.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .facts import Analysis
from .view import note_parts, simple_view

COLORS = {"support": "#34D399", "resistance": "#FB7185", "fib": "#FBBF24", "vp": "#8B5CF6"}
LABEL_AT = 1                  # zone labels start this many bars right of the last bar
VP_WIDTH = 40                 # bars spanned by the profile's busiest row
FIB_SWITCH = "פיבונאצ'י 38.2 / 50 / 61.8"


def _pine_string(text: Any, *more_lines: Any) -> str:
    """A Pine string literal: each line folded to one, quotes and backslashes escaped,
    lines joined by Pine's own newline escape."""
    clean = [" ".join(str(line).split()).replace("\\", "\\\\").replace('"', '\\"')
             for line in (text, *more_lines)]
    return '"' + "\\n".join(clean) + '"'


def _num(value: Any) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"not a finite number: {value!r}")
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _price(value: float) -> str:
    """A price as a reader wants it on a label: fewer digits on bigger prices."""
    return f"{value:.2f}" if value < 100 else f"{value:.1f}" if value < 1000 else f"{value:.0f}"


def _day_int(day: str) -> int:
    return int(day[:10].replace("-", ""))


class _Script:
    def __init__(self, bars: pd.DataFrame):
        self.days: list[str] = []
        self.body: list[str] = []
        days = bars["timestamp"].dt.strftime("%Y-%m-%d")
        self.closes = dict(zip(days, bars["close"].astype(float)))

    def anchor(self, day: str) -> str:
        """The Pine expression for the bar index of `day` (added to the anchors)."""
        day = day[:10]
        if day not in self.days:
            self.days.append(day)
        return f"array.get(anchorBars, {self.days.index(day)})"

    def add(self, condition: str, *lines: str) -> None:
        self.body.append(f"    if {condition}")
        self.body += [f"        {line}" for line in lines]


def _label(x: str, y: float, text: str | list[str], color: str, style: str = "label.style_label_left") -> str:
    lines = [text] if isinstance(text, str) else text
    return (f"label.new({x}, {_num(y)}, {_pine_string(*lines)}, xloc=xloc.bar_index, style={style}, "
            f"color=color.new({color}, 80), textcolor={color}, size=size.small)")


def pine_script(analysis: Analysis, bars: pd.DataFrame) -> str:
    view = simple_view(analysis)
    s = _Script(bars.reset_index(drop=True))
    right = f"bar_index + {LABEL_AT}"
    for item in view.values():
        if item["type"] != "zone":
            continue
        a = s.anchor(item["first_day"])
        color = COLORS[item["kind"]]
        name = "תמיכה" if item["kind"] == "support" else "התנגדות"
        # a note gets its own line: one Hebrew word and one number per line keeps the order
        text = [f"{name} {_price(item['low'])}-{_price(item['high'])}",
                *(" ".join(p for p in note_parts(n) if p) for n in item["notes"])]
        s.add(f"showZones and {a} >= 0",
              f"box.new({a}, {_num(item['high'])}, {right}, {_num(item['low'])}, xloc=xloc.bar_index, "
              f"border_color=color.new({color}, 55), bgcolor=color.new({color}, 85))",
              _label(right, (item["high"] + item["low"]) / 2, text, color))
    fib = view.get("fib")
    if fib and fib["levels"]:
        b = s.anchor(fib["end_day"])
        lines = []
        for key, price in fib["levels"].items():
            lines.append(f"line.new({b}, {_num(price)}, {right}, {_num(price)}, xloc=xloc.bar_index, "
                         f"color=color.new({COLORS['fib']}, 25), style=line.style_dashed)")
            lines.append(_label(b, price, " ".join(note_parts(key)), COLORS["fib"], "label.style_label_right"))
        s.add(f"showFib and {b} >= 0", *lines)
    vp = view.get("vp")
    if vp:
        edges, volume = vp["edges"], vp["volume"]
        top = max(volume) or 1.0
        gap = (edges[1] - edges[0]) * 0.08
        poc_row = volume.index(max(volume))
        rows = []
        for n, vol in enumerate(volume):
            width = round(VP_WIDTH * vol / top)
            if width < 1:
                continue
            fade = 45 if n == poc_row else 70 if vp["val"] <= edges[n] < vp["vah"] else 85
            rows.append(f"box.new(bar_index - {width}, {_num(edges[n + 1] - gap)}, bar_index, "
                        f"{_num(edges[n] + gap)}, xloc=xloc.bar_index, "
                        f"border_color=color.new({COLORS['vp']}, 100), bgcolor=color.new({COLORS['vp']}, {fade}))")
        if vp["poc_label"]:
            rows.append(_label(right, vp["poc"], f"POC {_price(vp['poc'])}", COLORS["vp"]))
        s.add("showVP", *rows)

    days = s.days
    closes = [s.closes.get(d, math.nan) for d in days]
    if any(not math.isfinite(c) for c in closes):
        raise ValueError("an anchor date is not in the bars")
    title = f"ניתוח {analysis.symbol} {analysis.last_day}"
    switches = ["showZones = input.bool(true, " + _pine_string("תמיכה והתנגדות") + ")"]
    switches.append(f"showFib = input.bool({'true' if fib and fib['show'] else 'false'}, "
                    f"{_pine_string(FIB_SWITCH)})" if fib and fib["levels"] else "showFib = false")
    switches.append(f"showVP = input.bool({'true' if vp and vp['show'] else 'false'}, "
                    f"{_pine_string('פרופיל נפח')})" if vp else "showVP = false")
    head = [
        "//@version=6",
        f"indicator({_pine_string(title)}, overlay=true, max_lines_count=50, max_boxes_count=100, "
        "max_labels_count=50)",
        "// Made by ta-screener's chart analyst from daily bars up to "
        f"{analysis.last_day}. Paste into the Pine Editor and add to a DAILY chart of {analysis.symbol}.",
        "// Not investment advice. Levels come from past prices; they are not a forecast.",
        "",
        *switches,
        "",
        f"string SYMBOL = {_pine_string(analysis.symbol)}",
        (f"var array<int> anchorDays = array.from({', '.join(str(_day_int(d)) for d in days)})"
         if days else "var array<int> anchorDays = array.new<int>()"),
        (f"var array<float> anchorCloses = array.from({', '.join(_num(c) for c in closes)})"
         if days else "var array<float> anchorCloses = array.new<float>()"),
        "var array<int> anchorBars = array.new<int>(array.size(anchorDays), -1)",
        "var int mismatched = 0",
        "",
        "int today = year * 10000 + month * 100 + dayofmonth",
        "if array.size(anchorDays) > 0",
        "    for i = 0 to array.size(anchorDays) - 1",
        "        if array.get(anchorDays, i) == today",
        "            array.set(anchorBars, i, bar_index)",
        "            if math.abs(close / array.get(anchorCloses, i) - 1) > 0.005",
        "                mismatched += 1",
        "",
        "plot(na, \"\", display=display.none, editable=false)",
        "",
        "var bool drawn = false",
        "if barstate.islast and not drawn",
        "    drawn := true",
        "    string warning = \"\"",
        "    if syminfo.prefix + \":\" + syminfo.ticker != SYMBOL",
        f"        warning := {_pine_string('הסקריפט נבנה עבור ' + analysis.symbol + '. פתח את הגרף של המניה הזו.')}",
        "    else if not timeframe.isdaily",
        f"        warning := {_pine_string('עבור לגרף יומי (1D).')}",
        "    else if mismatched > 0",
        f"        warning := {_pine_string('המחירים בגרף שונים מהנתונים של הניתוח. אולי מופעלת התאמה לדיבידנדים (adj).')}",
        "    if warning != \"\"",
        "        var table note = table.new(position.top_right, 1, 1, bgcolor=color.new(#130F20, 10))",
        "        table.cell(note, 0, 0, warning, text_color=#FB7185)",
    ]
    tail = [f"    {line}" for line in [
        "var table credit = table.new(position.bottom_right, 1, 1)",
        f"table.cell(credit, 0, 0, {_pine_string('ta-screener · לא ייעוץ השקעות')}, "
        "text_color=color.new(#8F86AE, 20), text_size=size.tiny)"]]
    return "\n".join(head + s.body + tail) + "\n"
