"""The written analysis (stage T3): one model call turns an analysis's facts into short
Hebrew sections for an ordinary reader; a program checks every section before it is shown.

The model gets the rules, the knowledge notes (knowledge/*.md: how the engine computes
each fact and how to talk about it, in our own words with their sources) and the facts,
plus what the chart shows (the simple view). It returns sections by name, each with the
fact keys it relies on. A section is dropped unless:
- it cites at least one fact, and only facts that exist;
- every number matches a fact value within 0.5% (small counts, indicator periods and the
  Fibonacci ratios are allowed), and every date is a fact's date;
- "bullish/bearish" and "rising/falling trend" appear only when a cited fact says so;
- it carries no advice, trading claim or forecast wording (the channels' own check).
Rejected or missing required sections get one retry with the reasons; what still fails
is left out, and the message says so. The model never draws: the chart is the simple view.
"""
from __future__ import annotations

import html
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..channels.generate import NUMBER, STRUCTURAL, banned, fact_numbers
from ..llm import LLM
from . import load_rules
from .facts import Analysis
from .view import note_parts, simple_view

KNOWLEDGE_DIR = Path(__file__).with_name("knowledge")
PARTS = {"headline": "בקצרה", "levels": "רמות", "trend": "מגמה",
         "fibonacci": "פיבונאצ'י", "volume": "נפח", "momentum": "מומנטום", "patterns": "תבניות",
         "up": "תרחיש עלייה", "down": "תרחיש ירידה"}
# The model writes these; the program writes the two scenarios from the up.*/down.* facts
# (review round 1: model-written scenarios were empty, circular or worded as forecasts).
WRITTEN = ("headline", "levels", "trend", "fibonacci", "volume", "momentum", "patterns")
REQUIRED = ("headline", "levels")
SCENARIOS = ("up", "down")
# Owner's choices after the first live texts (2026-09-24): short and simple, then shorter
# still, with a light by each section. The required sections, plus at most two others
# that matter now; one short sentence each.
MAX_CHARS = {"headline": 170, "levels": 230}
DEFAULT_MAX = 150
MAX_OPTIONAL = 2
# Lights: green / red / yellow judge a section's facts; the levels get a neutral pin and
# the scenarios arrows, since a direction is not a judgement (review round 1).
LIGHTS = {"green": "🟢", "red": "🔴", "yellow": "🟡", "pin": "📍", "up": "⬆️", "down": "⬇️"}
FIXED_LIGHT = {"levels": "pin", "up": "up", "down": "down"}
EFFORT = "high"

FORECAST = re.compile(r"(?<![א-ת])(?:[וש]?)(?:תוביל|יוביל|תגיע|יגיע|יעלה|תעלה|תרד)(?![א-ת])")
INTERNAL_ID = re.compile(r"\b(?:zone|tl|div|pat)_\d+\b|\bfib(?:_ext)?_\d+\b|\b[a-z]+[0-9]*\.[a-z_]+\b")
DATE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}|(?<![\d.])\d{1,2}/\d{1,2}(?:/\d{2,4})?(?![\d/])")
BULL, BEAR = ("שורי", "שורית", "שוריים", "שוריות"), ("דובי", "דובית", "דוביים", "דוביות")
UP_TREND = ("מגמה עולה", "מגמת עלייה", "מגמה חיובית")
DOWN_TREND = ("מגמה יורדת", "מגמת ירידה", "מגמה שלילית")
UP_WORDS, DOWN_WORDS = ("עולה", "עלייה", "שורי"), ("יורד", "ירידה", "דובי")

RULES = """You are the chart analyst of a Hebrew technical-analysis website. You explain one stock's
daily chart to an ordinary reader. A program computed every level and signal; you only explain
them. Write everything the reader sees ("text") in natural, professional Hebrew.

These rules are checked by a program; a section that breaks one is thrown away:
1. Use only the facts in the brief. Each section lists in "cites" the fact keys it relies on:
   at least one, spelled exactly as in the brief.
2. Every number you write must be the value of one of the facts. Prices exactly as in the
   facts, with two decimals, without a $ sign; percentages as in the facts. Do not compute
   new numbers: no differences, sums or percentages of your own.
3. Dates only as they appear in the facts, written DD/MM (the year only if it is not this one).
4. The words שורי / דובי, and "מגמה עולה" / "מגמה יורדת", only when a fact you cite says so.
   Never a pattern's textbook bias ("usually bullish"): only what its facts say.
5. No advice and no forecasts. Never tell anyone to buy, sell, enter, exit or set a stop; never
   say where the price will go or which scenario is more likely; no "will lead to", "will reach".
6. A target is "יעד לפי גובה התבנית (לא תחזית)", never a forecast.
7. Nothing outside the facts: no news, earnings, fundamentals, other stocks or market talk.
8. Never write a fact key or id in the text (zone_2, tl_1, fib_618, ma.stack): name things in
   Hebrew ("אזור התמיכה", "קו ההתנגדות").

The reader's message already opens with the ticker, the close, the day's change and the date,
and ends with the two scenarios, which the program writes from the up.* and down.* facts.
You write, ONE short sentence each, at most one of each:
- headline (required, <= 170 characters): the story now: the trend in words (ma.stack) and
  the key event, quoting the `event` fact as given; it must cite `event`. Never repeat the
  close, the date or the day's change.
- levels (required, <= 230 characters): the nearest resistance (level.r1) and support
  (level.s1) with their distance in %; the second ones only if close. With no level.r1 the
  price is at or near its yearly high (high_52w): say so; never "no resistance marked".
- At most TWO of these optional sections, only when they add to the picture now, the more
  important first (<= 150 characters each): patterns (a pat_* breakout: "המחיר פרץ מ.../
  נשבר מ..." (the price breaks out of a pattern, a pattern does not break out), with its
  state today from pat_N.state), trend (the averages or a trendline), momentum (RSI, MACD, a
  divergence: only if div_* facts exist), volume (the profile, the volume trend), fibonacci
  (only if the chart shows Fibonacci lines).

Plain words for an ordinary reader: explain a technical term in a few words the first time
("RSI (מדד המומנטום)", "סטייה (המחיר קבע שיא חדש והמומנטום לא)", "ממוצע 50 הימים").
Overbought and oversold are states, not signals. No filler, no repetition between sections.

Each written section has a light ("signal"): "green" when the facts it cites lean positive
for the price (price above the averages, MACD above its signal line, a bullish divergence, a
breakout that holds), "red" when they lean negative (a breakdown that holds, a bearish
divergence), "yellow" when they are neutral or mixed (a breakout the price has already
undone). The levels section's light is fixed. A section whose text says bearish (דובי)
cannot be green, one that says bullish (שורי) cannot be red.

"chart" in the brief lists exactly what the reader sees on the chart. Something that is not
drawn gets at most one short sentence. Name a line by what it does and its direction
together: "קו התנגדות עולה", "קו תמיכה יורד".

KNOWLEDGE (how the engine computes each fact, and how to talk about it):
"""


def knowledge() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8").strip() for p in sorted(KNOWLEDGE_DIR.glob("*.md")))


def system_prompt() -> str:
    return RULES + "\n" + knowledge()


def chart_lines(analysis: Analysis) -> list[str]:
    """What the reader sees on the chart, in words (the simple view)."""
    out = []
    for key, item in simple_view(analysis).items():
        if item["type"] == "zone":
            name = "support" if item["kind"] == "support" else "resistance"
            notes = [" ".join(p for p in note_parts(n) if p) for n in item["notes"]]
            out.append(f"{key}: {name} zone {item['low']}-{item['high']}"
                       + (f", its label also says: {', '.join(notes)}" if notes else ""))
        elif item["type"] == "fib":
            levels = ", ".join(item["levels"]) or "none (all joined zones)"
            out.append(f"Fibonacci lines: {levels}" if item["show"] else "Fibonacci: not drawn")
        elif item["type"] == "profile":
            out.append("volume profile: drawn" if item["show"] else "volume profile: not drawn")
        elif item["type"] == "ma":
            out.append("moving averages drawn: " + ", ".join(f"sma{n}" for n in item["periods"]))
        elif item["type"] == "extreme":
            out.append(f"{key}: a dashed line at the 52-week {'high' if key == 'hi52' else 'low'} {item['price']}")
        elif item["type"] == "pattern":
            out.append(f"{key}: the pattern's outline, its breakout marked, the target line labelled "
                       "'יעד (גובה התבנית)'")
    return out


def user_prompt(analysis: Analysis) -> str:
    brief = {"symbol": analysis.symbol, "last_day": analysis.last_day,
             "chart": chart_lines(analysis), "facts": analysis.facts}
    return "Write the analysis.\n\nBRIEF (JSON):\n" + json.dumps(brief, ensure_ascii=False, indent=1)


def response_schema() -> dict:
    part = {"type": "object", "additionalProperties": False,
            "required": ["part", "signal", "text", "cites"],
            "properties": {"part": {"type": "string", "enum": list(WRITTEN)},
                           "signal": {"type": "string", "enum": ["green", "red", "yellow"]},
                           "text": {"type": "string"},
                           "cites": {"type": "array", "items": {"type": "string"}}}}
    return {"type": "object", "additionalProperties": False, "required": ["parts"],
            "properties": {"parts": {"type": "array", "items": part}}}


# ------------------------------------------------------------------ checks
def allowed_numbers(facts: dict[str, dict], rules: dict[str, Any]) -> list[float]:
    """Fact values, plus the method's own constants a text may name (38.2%, MACD 12/26/9)."""
    extra = [r * 100 for r in [*rules["fib_retracements"], *rules["fib_extensions"]]]
    extra += [*rules["macd"], *rules["ma_periods"], rules["rsi_period"], rules["rsi_overbought"],
              rules["rsi_oversold"], rules["value_area"] * 100]
    return fact_numbers(facts) + [float(x) for x in extra]


def fact_dates(facts: dict[str, dict]) -> set[tuple[int, int, int]]:
    out = set()
    for fact in facts.values():
        value = fact.get("value")
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            y, m, d = (int(x) for x in value.split("-"))
            out.add((d, m, y))
    return out


def _date_ok(raw: str, known: set[tuple[int, int, int]]) -> bool:
    if "-" in raw:
        y, m, d = (int(x) for x in raw.split("-"))
        return (d, m, y) in known
    parts = [int(x) for x in raw.split("/")]
    d, m = parts[0], parts[1]
    if len(parts) == 2:
        return any((d, m) == (kd, km) for kd, km, _ in known)
    y = parts[2] + 2000 if parts[2] < 100 else parts[2]
    return (d, m, y) in known


def unmatched_numbers(text: str, allowed: list[float]) -> list[str]:
    bad = []
    for raw in NUMBER.findall(DATE_TEXT.sub(" ", text)):
        x = abs(float(raw.replace(",", "")))
        if (x.is_integer() and x < 20) or x in STRUCTURAL:
            continue
        if not any(abs(x - v) <= max(0.005 * v, 0.051) for v in allowed):
            bad.append(raw)
    return bad


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    """A word, allowing up to two Hebrew prefix letters (ו, ה, ש, ב, ל, כ, מ)."""
    return any(re.search(rf"(?<![א-ת])[והשבלכמ]{{0,2}}{w}(?![א-ת])", text) for w in words)


def direction_problem(text: str, cited: list[dict]) -> str | None:
    values = " ".join(str(f.get("value", "")) for f in cited)
    for words, needed, name in ((BULL, ("שור",), "bullish"), (BEAR, ("דוב",), "bearish")):
        if _has_word(text, words) and not any(n in values for n in needed):
            return f"says {name} but no cited fact does"
    if any(p in text for p in UP_TREND) and not any(w in values for w in UP_WORDS):
        return "says a rising trend but no cited fact does"
    if any(p in text for p in DOWN_TREND) and not any(w in values for w in DOWN_WORDS):
        return "says a falling trend but no cited fact does"
    return None


def part_problem(part: dict, facts: dict[str, dict], numbers: list[float],
                 dates: set[tuple[int, int, int]]) -> str | None:
    name = part.get("part")
    if name not in WRITTEN:
        return f"unknown section {name!r}"
    text = " ".join(str(part.get("text", "")).split())
    limit = MAX_CHARS.get(name, DEFAULT_MAX)
    if not text or len(text) > limit:
        return f"text length {len(text)} (1..{limit})"
    cites = [c for c in part.get("cites") or [] if isinstance(c, str)]
    if not cites:
        return "cites no fact"
    unknown = [c for c in cites if c not in facts]
    if unknown:
        return f"cites unknown fact keys {unknown[:3]}"
    ids = INTERNAL_ID.findall(text)
    if ids:
        return f"internal ids in the text (name them in Hebrew): {ids[:3]}"
    wrong_dates = [d for d in DATE_TEXT.findall(text) if not _date_ok(d, dates)]
    if wrong_dates:
        return f"dates not in the facts: {wrong_dates[:3]}"
    bad = unmatched_numbers(text, numbers)
    if bad:
        return f"numbers not in the facts: {bad[:3]}"
    words = banned(text)
    if words:
        return f"advice or forecast wording: {words[:3]}"
    forecast = FORECAST.findall(text)
    if forecast:
        return f"forecast wording: {forecast[:3]}"
    # the story and the levels come from the facts the chart was drawn from
    if name == "headline" and "event" in facts and "event" not in cites:
        return "must cite event (the key event now)"
    if name == "levels" and any(k.startswith("level.") for k in facts) \
            and not any(c.startswith("level.") for c in cites):
        return "must cite the level.* facts (the zones the chart shows)"
    problem = direction_problem(text, [facts[c] for c in cites])
    if problem or name in FIXED_LIGHT:
        return problem
    light = part.get("signal")
    if light not in LIGHTS:
        return f"no light (one of {', '.join(LIGHTS)})"
    if light == "green" and _has_word(text, BEAR) and not _has_word(text, BULL):
        return "a green light on a bearish text"
    if light == "red" and _has_word(text, BULL) and not _has_word(text, BEAR):
        return "a red light on a bullish text"
    return None


def _finish(text: str) -> str:
    text = " ".join(text.split())
    if "יעד" in text and "גובה התבנית" not in text and "כלל המדידה" not in text:
        text += " (יעד לפי גובה התבנית, לא תחזית)"
    return text


def scenario_parts(facts: dict[str, dict]) -> list[dict[str, Any]]:
    """The two scenarios, written by the program from the up.* / down.* facts: always
    conditional, always with the trigger, the next level and what cancels it, in % from
    the close (review round 1: model-written ones were empty, circular or forecasts)."""
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    out = []
    for side, up in (("up", True), ("down", False)):
        trigger, cites = value(f"{side}.trigger"), []
        where = "מעל הסגירה" if up else "מתחת לסגירה"
        if isinstance(trigger, (int, float)):
            text = (f"בסגירה {'מעל ' if up else 'מתחת ל-'}{trigger:.2f} "
                    f"({value(f'{side}.trigger_what')}, {value(f'{side}.trigger_pct'):.1f}% {where})")
            cites += [f"{side}.trigger", f"{side}.trigger_pct"]
            nxt = value(f"{side}.next")
            if isinstance(nxt, (int, float)):
                text += (f", הרמה הבאה היא {nxt:.2f} ({value(f'{side}.next_what')}, "
                         f"{value(f'{side}.next_pct'):.1f}% {where})")
                cites += [f"{side}.next", f"{side}.next_pct"]
            elif value(f"{side}.no_next"):
                text += f"; {value(f'{side}.no_next')}"
                cites.append(f"{side}.no_next")
            cancel = value(f"{side}.cancel")
            if isinstance(cancel, (int, float)):
                text += f". התרחיש מתבטל בסגירה חזרה {'מתחת ל-' if up else 'מעל '}{cancel:.2f}."
                cites.append(f"{side}.cancel")
            else:
                text += "."
        elif value(f"{side}.no_next"):
            text, cites = f"{value(f'{side}.no_next')}.", [f"{side}.no_next"]
        else:
            continue
        out.append({"part": side, "signal": side, "text": text, "cites": cites, "title": PARTS[side]})
    return out


def check_parts(raw: dict, facts: dict[str, dict], rules: dict[str, Any]) -> tuple[dict[str, dict], list[dict]]:
    """The sections that pass (first of each name) and what was dropped, with reasons."""
    numbers, dates = allowed_numbers(facts, rules), fact_dates(facts)
    kept: dict[str, dict] = {}
    dropped: list[dict] = []
    for part in raw.get("parts") or []:
        name = part.get("part")
        optional = sum(1 for n in kept if n not in REQUIRED)
        problem = ("repeated section" if name in kept else
                   "more optional sections than allowed" if name in WRITTEN and name not in REQUIRED
                   and optional >= MAX_OPTIONAL else part_problem(part, facts, numbers, dates))
        if problem:
            dropped.append({"part": name, "reason": problem, "text": str(part.get("text", ""))[:300]})
            continue
        kept[name] = {"part": name, "signal": FIXED_LIGHT.get(name, part.get("signal")),
                      "text": _finish(part["text"]),
                      "cites": [c for c in part["cites"] if isinstance(c, str)]}
    return kept, dropped


def write(analysis: Analysis, llm: LLM, *, rules: dict[str, Any] | None = None,
          now=lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    """The written analysis: sections in display order, what was dropped, and the usage.
    The model writes the headline, the levels and up to two optional sections; the
    program adds the two scenarios."""
    rules = rules or load_rules()
    started = time.monotonic()
    system, user, schema = system_prompt(), user_prompt(analysis), response_schema()
    raw, usage = llm.complete(system=system, user=user, schema=schema)
    kept, dropped = check_parts(raw, analysis.facts, rules)
    usages = [usage]
    # a rejected section gets one rewrite with its reason, the optional ones too (review
    # round 1: the key pattern was lost over one phrase); over the cap is not a fault
    retry = {d["part"]: d["reason"] for d in dropped if d["part"] in WRITTEN and d["part"] not in kept
             and d["reason"] != "more optional sections than allowed"}
    retry.update({name: "missing" for name in REQUIRED if name not in kept and name not in retry})
    if retry:
        fix = (user + "\n\nYour previous answer was checked by the program. Write again ONLY these "
               "sections, fixing the problem:\n" + "\n".join(f"- {k}: {v}" for k, v in retry.items()))
        raw2, usage2 = llm.complete(system=system, user=fix, schema=schema)
        usages.append(usage2)
        kept2, dropped2 = check_parts(raw2, analysis.facts, rules)
        for name, part in kept2.items():
            optional = sum(1 for n in kept if n not in REQUIRED)
            if name in retry and name not in kept and (name in REQUIRED or optional < MAX_OPTIONAL):
                kept[name] = part
        dropped += [{**d, "retry": True} for d in dropped2]
    parts = [{**kept[name], "title": PARTS[name]} for name in PARTS if name in kept]
    parts += scenario_parts(analysis.facts)
    value = lambda key: (analysis.facts.get(key) or {}).get("value")      # noqa: E731
    return {"symbol": analysis.symbol, "last_day": analysis.last_day, "parts": parts,
            "close": value("close"), "change_1d_pct": value("change_1d_pct"),
            "omitted": [name for name in REQUIRED if name not in kept],
            "dropped": dropped, "model": getattr(llm, "name", "?"), "usage": usages,
            "seconds": round(time.monotonic() - started, 1),
            "generated_at": now().isoformat(timespec="seconds")}


# ------------------------------------------------------------------ the message
DISCLAIMER = "לא ייעוץ השקעות. כל הרמות מחושבות ממחירי עבר, והן אינן תחזית."


def _day(day: str) -> str:
    y, m, d = day[:10].split("-")
    return f"{d}/{m}/{y}"


TELEGRAM_LIMIT = 4096


def telegram_html(written: dict[str, Any]) -> str:
    """The message (Telegram HTML: every text escaped; only <b> and <i> are ours). Too long
    for one message: optional sections go, last first (never cut in the middle of a tag)."""
    ticker = written["symbol"].split(":")[-1]
    parts = list(written["parts"])
    close, change = written.get("close"), written.get("change_1d_pct")
    quote = ""
    if isinstance(close, (int, float)):
        quote = f"סגירה {close:,.2f}"
        if isinstance(change, (int, float)):
            quote += f" ({'🔺' if change >= 0 else '🔻'} {abs(change):.2f}%)"
        quote += " · "
    while True:
        lines = [f"<b>📊 ניתוח טכני · {html.escape(ticker)}</b>",
                 html.escape(f"{quote}{_day(written['last_day'])}")]
        group = None
        for part in parts:
            light = LIGHTS.get(part.get("signal"), "")
            now = ("head" if part["part"] == "headline" else
                   "scenario" if part["part"] in SCENARIOS else "body")
            if now != group:
                lines.append("")                 # a blank line between the three groups
                group = now
            title = "" if now == "head" else f"<b>{html.escape(part['title'])}:</b> "
            lines.append(f"{light} {title}{html.escape(part['text'])}".strip())
        lines.append("")
        if written["omitted"]:
            names = ", ".join(PARTS[n] for n in written["omitted"])
            lines += [html.escape(f"(הושמט: {names}. הטקסט לא עבר את הבדיקה מול הנתונים.)"), ""]
        lines.append(f"<i>{html.escape(DISCLAIMER)}</i>")
        message = "\n".join(lines)
        optional = [p for p in parts if p["part"] not in REQUIRED and p["part"] not in SCENARIOS]
        if len(message) <= TELEGRAM_LIMIT or not optional:
            return message
        parts.remove(optional[-1])


def photo_caption(analysis: Analysis) -> str:
    """The chart's caption: the close and the nearest levels, the ones the chart draws."""
    facts = analysis.facts

    def zone(key: str) -> str | None:
        low, high = (facts.get(f"{key}.low") or {}).get("value"), (facts.get(f"{key}.high") or {}).get("value")
        return f"{low:,.2f}-{high:,.2f}" if isinstance(low, (int, float)) and isinstance(high, (int, float)) else None

    parts = [analysis.symbol.split(":")[-1], _day(analysis.last_day)]
    close = (facts.get("close") or {}).get("value")
    if isinstance(close, (int, float)):
        parts.append(f"סגירה {close:,.2f}")
    if zone("level.r1"):
        parts.append(f"🔴 התנגדות {zone('level.r1')}")
    if zone("level.s1"):
        parts.append(f"🟢 תמיכה {zone('level.s1')}")
    return " · ".join(parts)


def pine_caption(analysis: Analysis) -> str:
    return (f"סקריפט ל-TradingView: פתח גרף יומי (1D) של {analysis.symbol}, "
            "ב-Pine Editor מחק הכול, הדבק, ולחץ Add to chart.")
