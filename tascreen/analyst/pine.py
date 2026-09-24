"""The analysis as a TradingView Pine Script (v6): pasted once into the Pine Editor, it
draws the analysis's zones, trendlines, Fibonacci levels, divergences, patterns and the
volume POC on the owner's own chart. (TradingView's MCP server has no drawing tools, so
this is the way onto the owner's charts.)

How it anchors: every drawing starts at a session date; the script finds those dates by
year/month/day on the chart's own bars (exchange time) and draws with xloc.bar_index, so
no time zone or bar time has to be guessed. It checks that it runs on the right symbol,
on a daily chart, and that the chart's closes on the anchor dates match the analysis's
(within 0.5%; a chart adjusted for dividends, or another feed, shows a warning).

Only data is generated: numbers formatted from floats and text through `_pine_string`
(escaped, one line), into a fixed layout. Everything is drawn once, on the last bar.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .facts import Analysis

COLORS = {"support": "#34D399", "resistance": "#FB7185", "fib": "#FBBF24", "line": "#C4B5FD",
          "bull": "#34D399", "bear": "#FB7185", "poc": "#8B5CF6", "ma50": "#FBBF24",
          "ma150": "#67E8F9", "ma200": "#A78BFA"}
RIGHT = 12                    # bars to the right of the last one where lines end and labels sit


def _pine_string(text: Any) -> str:
    """A Pine string literal: one line, quotes and backslashes escaped."""
    clean = " ".join(str(text).split()).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{clean}"'


def _num(value: Any) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"not a finite number: {value!r}")
    return f"{number:.4f}".rstrip("0").rstrip(".")


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

    def when(self, *anchors: str) -> str:
        return " and ".join(f"{a} >= 0" for a in anchors) or "true"

    def add(self, condition: str, *lines: str) -> None:
        self.body.append(f"    if {condition}")
        self.body += [f"        {line}" for line in lines]


def _label(x: str, y: float, text: str, color: str) -> str:
    return (f"label.new({x}, {_num(y)}, {_pine_string(text)}, xloc=xloc.bar_index, "
            f"style=label.style_label_left, color=color.new({color}, 70), textcolor={color}, "
            "size=size.small)")


def pine_script(analysis: Analysis, bars: pd.DataFrame, drawings: list[str] | None = None) -> str:
    from .chart import default_drawings

    wanted = [d for d in (drawings or default_drawings(analysis)) if d in analysis.drawings]
    s = _Script(bars.reset_index(drop=True))
    right = f"bar_index + {RIGHT}"
    show_ma = False
    for key in wanted:
        item = analysis.drawings[key]
        kind = item["type"]
        if kind == "zone":
            a = s.anchor(item["first_day"])
            color = COLORS[item["kind"]]
            name = "תמיכה" if item["kind"] == "support" else "התנגדות"
            s.add(s.when(a),
                  f"box.new({a}, {_num(item['high'])}, {right}, {_num(item['low'])}, xloc=xloc.bar_index, "
                  f"border_color=color.new({color}, 40), bgcolor=color.new({color}, 86), "
                  "border_style=line.style_dashed)",
                  _label(right, (item["high"] + item["low"]) / 2,
                         f"{name} {item['low']:.2f}-{item['high']:.2f}", color))
        elif kind == "line":
            a = s.anchor(item["day1"])
            color = COLORS[item["kind"]]
            name = "קו תמיכה" if item["kind"] == "support" else "קו התנגדות"
            s.add(s.when(a),
                  f"line.new({a}, {_num(item['price1'])}, bar_index, {_num(item['price2'])}, "
                  f"xloc=xloc.bar_index, extend=extend.right, color={color}, width=2)",
                  _label(right, item["price2"], name, color))
        elif kind == "fib":
            a, b = s.anchor(item["start_day"]), s.anchor(item["end_day"])
            lines = [f"line.new({a}, {_num(item['start'])}, {b}, {_num(item['end'])}, xloc=xloc.bar_index, "
                     f"color={COLORS['line']}, style=line.style_dotted)"]
            for name, price in item["levels"].items():
                ratio = int(name.rsplit("_", 1)[1]) / 10
                lines.append(f"line.new({b}, {_num(price)}, {right}, {_num(price)}, xloc=xloc.bar_index, "
                             f"color=color.new({COLORS['fib']}, 30), style=line.style_dashed)")
                lines.append(_label(right, price, f"Fib {ratio:g}% {price:.2f}", COLORS["fib"]))
            s.add(s.when(a, b), *lines)
        elif kind == "divergence":
            a, b = s.anchor(item["day1"]), s.anchor(item["day2"])
            color = COLORS["bull" if item["kind"] == "bullish" else "bear"]
            name = f"סטייה {'שורית' if item['kind'] == 'bullish' else 'דובית'} {item['indicator'].upper()}"
            s.add(s.when(a, b),
                  f"line.new({a}, {_num(item['price1'])}, {b}, {_num(item['price2'])}, xloc=xloc.bar_index, "
                  f"color={color}, style=line.style_dashed, width=2)",
                  _label(b, item["price2"], name, color))
        elif kind == "pattern" and item["family"] == "chart":
            color = COLORS["bull" if item["direction"] == "bullish" else "bear"
                           if item["direction"] == "bearish" else "line"]
            for line in item["lines"]:
                a, b = s.anchor(line["x1"]), s.anchor(line["x2"])
                s.add(s.when(a, b), f"line.new({a}, {_num(line['y1'])}, {b}, {_num(line['y2'])}, "
                                    f"xloc=xloc.bar_index, color={COLORS['line']}, width=2)")
            end = s.anchor(item["end"])
            for level, name, style in ((item.get("target"), "יעד (כלל המדידה, לא תחזית)", "line.style_dashed"),
                                       (item.get("invalidation"), "ביטול", "line.style_dotted")):
                if level is not None and math.isfinite(float(level)):
                    s.add(s.when(end),
                          f"line.new({end}, {_num(level)}, {right}, {_num(level)}, xloc=xloc.bar_index, "
                          f"color={color}, style={style})",
                          _label(right, float(level), f"{name} {float(level):.2f}", color))
        elif kind == "profile":
            s.add("true", f"line.new(bar_index - 60, {_num(item['poc'])}, {right}, {_num(item['poc'])}, "
                          f"xloc=xloc.bar_index, color=color.new({COLORS['poc']}, 30), style=line.style_dotted)",
                  _label(right, item["poc"], f"POC {item['poc']:.2f}", COLORS["poc"]))
        elif kind == "ma":
            show_ma = True

    days = s.days
    closes = [s.closes.get(d, math.nan) for d in days]
    if any(not math.isfinite(c) for c in closes):
        raise ValueError("an anchor date is not in the bars")
    title = f"ניתוח {analysis.symbol} {analysis.last_day}"
    head = [
        "//@version=6",
        f"indicator({_pine_string(title)}, overlay=true, max_lines_count=300, max_boxes_count=60, "
        "max_labels_count=200)",
        "// Made by ta-screener's chart analyst from daily bars up to "
        f"{analysis.last_day}. Paste into the Pine Editor and add to a DAILY chart of {analysis.symbol}.",
        "// Not investment advice. A target follows the book's measure rule; it is not a forecast.",
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
        f"plot({'ta.sma(close, 50)' if show_ma else 'na'}, 'SMA 50', color={COLORS['ma50']})",
        f"plot({'ta.sma(close, 150)' if show_ma else 'na'}, 'SMA 150', color={COLORS['ma150']})",
        f"plot({'ta.sma(close, 200)' if show_ma else 'na'}, 'SMA 200', color={COLORS['ma200']})",
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
        f"var table credit = table.new(position.bottom_right, 1, 1)",
        f"table.cell(credit, 0, 0, {_pine_string('ta-screener · לא ייעוץ השקעות')}, "
        "text_color=color.new(#8F86AE, 20), text_size=size.tiny)"]]
    return "\n".join(head + s.body + tail) + "\n"
