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

from ..channels.generate import NUMBER, STRUCTURAL, banned, fact_numbers, finish_text
from ..llm import LLM
from . import load_rules
from .facts import Analysis
from .view import note_parts, simple_view

KNOWLEDGE_DIR = Path(__file__).with_name("knowledge")
PARTS = {"headline": "בקצרה", "levels": "תמיכה והתנגדות", "trend": "מגמה",
         "fibonacci": "פיבונאצ'י", "volume": "נפח", "momentum": "מומנטום", "patterns": "תבניות",
         "up": "תרחיש עולה", "down": "תרחיש יורד"}
REQUIRED = ("headline", "levels", "up", "down")
# Owner's choice after the first live text (2026-09-24): short and simple. The four
# required sections, plus at most two others that matter now, one sentence each.
MAX_CHARS = {"headline": 200, "levels": 300, "up": 250, "down": 250}
DEFAULT_MAX = 180
MAX_OPTIONAL = 2
EFFORT = "high"

INTERNAL_ID = re.compile(r"\b(?:zone|tl|div|pat)_\d+\b|\bfib(?:_ext)?_\d+\b|\b[a-z]+[0-9]*\.[a-z_]+\b")
DATE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}|(?<![\d.])\d{1,2}/\d{1,2}(?:/\d{2,4})?(?![\d/])")
BULL, BEAR = ("שורי", "שורית", "שוריים", "שוריות"), ("דובי", "דובית", "דוביים", "דוביות")
UP_TREND = ("מגמה עולה", "מגמת עלייה", "מגמה חיובית")
DOWN_TREND = ("מגמה יורדת", "מגמת ירידה", "מגמה שלילית")
UP_WORDS, DOWN_WORDS = ("עולה", "עלייה", "שורי"), ("יורד", "ירידה", "דובי")

RULES = """You are the chart analyst of a Hebrew technical-analysis website. You explain one stock's
daily chart to an ordinary reader. A program computed every level and signal; you only explain
them. Write everything the reader sees ("text") in Hebrew.

These rules are checked by a program; a section that breaks one is thrown away:
1. Use only the facts in the brief. Each section lists in "cites" the fact keys it relies on:
   at least one, spelled exactly as in the brief.
2. Every number you write must be the value of one of the facts (you may round it). Do not
   compute new numbers: no differences, sums or percentages of your own.
3. Dates only as they appear in the facts, written DD/MM/YYYY.
4. The words שורי / דובי, and "מגמה עולה" / "מגמה יורדת", only when a fact you cite says so.
5. No advice and no forecasts. Never tell anyone to buy, sell, enter, exit or set a stop; never
   say where the price will go or which scenario is more likely.
6. A target is always "יעד לפי כלל המדידה", never a forecast.
7. Nothing outside the facts: no news, earnings, fundamentals, other stocks or market talk.
8. Never write a fact key or id in the text (zone_2, tl_1, fib_618, ma.stack): name things in
   Hebrew ("אזור התמיכה", "קו ההתנגדות").

Keep it short and simple: the whole analysis is read in under a minute. Sections ("part"),
at most one of each:
- headline (required, 1-2 short sentences, <= 200 characters): the picture now: where the
  price stands between the nearest support and the nearest resistance.
- levels (required, 1-2 sentences, <= 300 characters): the support and resistance zones
  drawn on the chart, nearest first.
- up (required, one sentence, <= 250 characters): the upward scenario, conditional: which
  close would start it, the next level on the way, and which level would cancel it.
- down (required, one sentence, <= 250 characters): the same, downward.
- At most TWO of these optional sections, only when they change the picture now, the more
  important first, ONE short sentence each (<= 180 characters):
  trend (the moving averages, a trendline), fibonacci (only if the chart shows Fibonacci
  lines or a zone carries a Fibonacci note), volume (the profile, the volume trend),
  momentum (RSI, MACD, a divergence), patterns (only if the brief has pat_* facts).

"chart" in the brief lists exactly what the reader sees on the chart. Something that is not
drawn (a trendline, a divergence, a pattern, the averages) gets at most one short sentence.
Name a line by what it does and its direction together: "קו התנגדות עולה", "קו תמיכה יורד".

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
    return out


def user_prompt(analysis: Analysis) -> str:
    brief = {"symbol": analysis.symbol, "last_day": analysis.last_day,
             "chart": chart_lines(analysis), "facts": analysis.facts}
    return "Write the analysis.\n\nBRIEF (JSON):\n" + json.dumps(brief, ensure_ascii=False, indent=1)


def response_schema() -> dict:
    part = {"type": "object", "additionalProperties": False, "required": ["part", "text", "cites"],
            "properties": {"part": {"type": "string", "enum": list(PARTS)},
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
    if name not in PARTS:
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
    return direction_problem(text, [facts[c] for c in cites])


def check_parts(raw: dict, facts: dict[str, dict], rules: dict[str, Any]) -> tuple[dict[str, dict], list[dict]]:
    """The sections that pass (first of each name) and what was dropped, with reasons."""
    numbers, dates = allowed_numbers(facts, rules), fact_dates(facts)
    kept: dict[str, dict] = {}
    dropped: list[dict] = []
    for part in raw.get("parts") or []:
        name = part.get("part")
        optional = sum(1 for n in kept if n not in REQUIRED)
        problem = ("repeated section" if name in kept else
                   "more optional sections than allowed" if name in PARTS and name not in REQUIRED
                   and optional >= MAX_OPTIONAL else part_problem(part, facts, numbers, dates))
        if problem:
            dropped.append({"part": name, "reason": problem, "text": str(part.get("text", ""))[:300]})
            continue
        kept[name] = {"part": name, "text": finish_text(part["text"], live=False),
                      "cites": [c for c in part["cites"] if isinstance(c, str)]}
    return kept, dropped


def write(analysis: Analysis, llm: LLM, *, rules: dict[str, Any] | None = None,
          now=lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    """The written analysis: sections in display order, what was dropped, and the usage.
    Only the required sections are retried; an optional one that fails is simply left out."""
    rules = rules or load_rules()
    started = time.monotonic()
    system, user, schema = system_prompt(), user_prompt(analysis), response_schema()
    raw, usage = llm.complete(system=system, user=user, schema=schema)
    kept, dropped = check_parts(raw, analysis.facts, rules)
    usages = [usage]
    retry = {d["part"]: d["reason"] for d in dropped if d["part"] in REQUIRED and d["part"] not in kept}
    retry.update({name: "missing" for name in REQUIRED if name not in kept and name not in retry})
    if retry:
        fix = (user + "\n\nYour previous answer was checked by the program. Write again ONLY these "
               "sections, fixing the problem:\n" + "\n".join(f"- {k}: {v}" for k, v in retry.items()))
        raw2, usage2 = llm.complete(system=system, user=fix, schema=schema)
        usages.append(usage2)
        kept2, dropped2 = check_parts(raw2, analysis.facts, rules)
        for name, part in kept2.items():
            if name in retry and name not in kept:
                kept[name] = part
        dropped += [{**d, "retry": True} for d in dropped2]
    parts = [{**kept[name], "title": PARTS[name]} for name in PARTS if name in kept]
    return {"symbol": analysis.symbol, "last_day": analysis.last_day, "parts": parts,
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
    while True:
        lines = [f"<b>ניתוח טכני · {html.escape(ticker)} · {_day(written['last_day'])}</b>", ""]
        for part in parts:
            if part["part"] == "headline":
                lines += [html.escape(part["text"]), ""]
            else:
                lines += [f"<b>{html.escape(part['title'])}</b>", html.escape(part["text"]), ""]
        if written["omitted"]:
            names = ", ".join(PARTS[n] for n in written["omitted"])
            lines += [html.escape(f"(הושמט: {names}. הטקסט לא עבר את הבדיקה מול הנתונים.)"), ""]
        lines.append(f"<i>{html.escape(DISCLAIMER)}</i>")
        message = "\n".join(lines)
        optional = [p for p in parts if p["part"] not in REQUIRED]
        if len(message) <= TELEGRAM_LIMIT or not optional:
            return message
        parts.remove(optional[-1])


def photo_caption(analysis: Analysis) -> str:
    view = simple_view(analysis)
    shown = ["תמיכה והתנגדות"]
    if (view.get("fib") or {}).get("show"):
        shown.append("פיבונאצ'י")
    if (view.get("vp") or {}).get("show"):
        shown.append("פרופיל נפח")
    return f"{analysis.symbol} · {_day(analysis.last_day)} · {', '.join(shown)}"


def pine_caption(analysis: Analysis) -> str:
    return (f"סקריפט ל-TradingView: פתח גרף יומי (1D) של {analysis.symbol}, "
            "ב-Pine Editor מחק הכול, הדבק, ולחץ Add to chart.")
