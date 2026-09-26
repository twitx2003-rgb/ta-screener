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
The program writes the levels line and the two scenarios from the level map (review round 3:
the model's levels line and the scenarios named different zones).
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
from .view import FIB, HIGH52, LOW52, note_parts, simple_view

KNOWLEDGE_DIR = Path(__file__).with_name("knowledge")
PARTS = {"headline": "בקצרה", "levels": "רמות", "trend": "מגמה",
         "fibonacci": FIB, "volume": "נפח", "momentum": "מומנטום", "patterns": "תבניות",
         "up": "תרחיש עלייה", "down": "תרחיש ירידה"}
# The model writes these; the program writes the levels line and the two scenarios from the
# level.* / up.* / down.* facts (review round 1: model-written scenarios were empty, circular
# or worded as forecasts; round 3: the model's levels line named other zones).
WRITTEN = ("headline", "trend", "fibonacci", "volume", "momentum", "patterns")
REQUIRED = ("headline",)
SCENARIOS = ("up", "down")
# Owner's choices after the first live texts (2026-09-24): short and simple, then shorter
# still, with a light by each section. The required sections, plus at most two others
# that matter now; one short sentence each.
MAX_CHARS = {"headline": 170}
DEFAULT_MAX = 150
MAX_OPTIONAL = 2
# Lights: green / red / yellow judge a section's facts; the levels get a neutral pin and
# the scenarios arrows, since a direction is not a judgement (review round 1).
LIGHTS = {"green": "🟢", "red": "🔴", "yellow": "🟡", "pin": "📍", "up": "⬆️", "down": "⬇️"}
FIXED_LIGHT = {"levels": "pin", "up": "up", "down": "down"}
EFFORT = "high"

DOWN_BREAK = re.compile(r"(?<![א-ת])(?:ו?)(?:נפרץ|נפרצה|פרץ|פרצה)(?![א-ת])")
PRICE_BROKEN = re.compile(r"המחיר נשבר")
LATIN_START = re.compile(r"^[A-Za-z]")
YEAR = re.compile(r"(?<![\d/])(\d{1,2}/\d{1,2})/(?:20)?\d{2}(?![\d/])")
DOLLAR = re.compile(r"\s*\$|\s*דולר(?![א-ת])")
FORECAST = re.compile(r"(?<![א-ת])(?:[וש]?)(?:תוביל|יוביל|תגיע|יגיע|יעלה|תעלה|תרד)(?![א-ת])")
JARGON = re.compile(r"ATR|(?<![א-ת])(?:ה|ו|ש|ב)?(?:תנופה|רצועה|רצועת)(?![א-ת])|קו האות")
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
   facts, with two decimals, never with $ or "דולר"; percentages as in the facts. Do not
   compute new numbers: no differences, sums or percentages of your own.
3. Dates only as they appear in the facts, written DD/MM, never with the year; the last
   session is "היום".
4. The words שורי / דובי, and "מגמת עלייה" / "מגמת ירידה", only when a fact you cite says so.
   Never a pattern's textbook bias ("usually bullish"): only what its facts say.
5. No advice and no forecasts. Never tell anyone to buy, sell, enter, exit or set a stop; never
   say where the price will go or which scenario is more likely; no "will lead to", "will reach".
6. A target is "יעד לפי גובה התבנית", never a forecast, and only for a pattern that has broken
   out: for a pattern still forming, give its breakout line only. Never write "(לא תחזית)": the
   message's footer says it once for everything.
7. Nothing outside the facts: no news, earnings, fundamentals, other stocks or market talk.
8. Never write a fact key or id in the text (zone_2, tl_1, fib_618, ma.stack): name things in
   Hebrew ("אזור התמיכה", "קו ההתנגדות").
9. Direction words: up is "פריצה" / "המחיר פרץ מעל", down is "שבירה" / "המחיר ירד אל מתחת ל" /
   "נסגר מתחת ל". Never "נפרץ", "פרוצה" or "פרץ" for a downward break, never "המחיר נשבר".
10. After the light, a section starts with a Hebrew word, never with the ticker or a Latin
   term (a right-to-left line that starts in Latin letters is shown left to right).
11. The momentum section only when the RSI is 70 or more, 30 or less, or a divergence
   exists; overbought and oversold are states, not signals: that section's light is yellow.
   The MACD only as its words in macd.momentum; always "מומנטום", never "תנופה" or "קו האות".
12. Never "ATR" in the text: a distance is in percent (vs_sma50_pct, the stretch fact).
13. Volume only in the words of its fact (pat_N.breakout_volume, zone_N.broken_volume,
   volume.last, volume.spike): "בנפח גבוה, פי 1.9 מהממוצע", "בנפח רגיל", never a bare ratio.
14. A trendline (tl_N) only if the chart draws it (see "chart"); say its price.

The reader's message already opens with the ticker, the close, the day's change and the date.
After your headline the program writes the levels line (level.r1 and level.s1, with what is in
them) and, at the end, the two scenarios (up.* and down.*): never repeat their numbers.
You write, ONE short sentence each, at most one of each:
- headline (required, <= 170 characters, at most two numbers): a lead of two to four words
  and a colon, then the key event, quoting the `event` fact as given (it must cite `event`).
  The lead is the trend in plain words and agrees with the light: "מגמת עלייה:", "מגמת
  ירידה:", "אין מגמה ברורה:", "מגמת עלייה, בתיקון:" (yellow), "מגמת עלייה, אחרי שבירה:"
  (yellow). Never repeat the close, the date or the day's change.
- At most TWO of these optional sections, only when they add to the picture now, the more
  important first (<= 150 characters each): patterns (a pat_* breakout or failure: what the
  headline did not say, such as the breakout day's volume in words, the target or the line's
  price today), trend (the averages and their direction, the stretch fact, a drawn trendline
  with its price, always saying whether it is above or below the price), momentum (see rule
  11), volume (volume.spike: an unusual day of the last few), fibonacci (only if the chart
  shows Fibonacci lines: the nearest drawn level, and the move it measures from-to with dates).
  A stretched price (the stretch fact) or an RSI of 30-35 / 65-70 is worth a yellow section.

Never repeat between sections: a number or date the headline gave is not given again; the
averages' direction only under trend. A section that would only repeat the headline is left
out. Averages are "ממוצע 50 יום", "ממוצע 150 יום", "ממוצע 200 יום", never "הממוצע הבינוני".

Plain words for an ordinary reader, the term at most once in brackets: "ה-RSI (מדד
המומנטום)"; the MACD as macd.momentum says it ("המומנטום שלילי ומתחזק כלפי מטה"); "סטייה
חיובית: המחיר רשם שפל חדש והמומנטום לא"; "תבנית ראש וכתפיים (שלוש פסגות, האמצעית הגבוהה)";
"פסגה משולשת (שלוש פסגות באותו גובה)". Always "ה-RSI", "ה-MACD"; the RSI as a whole number
when it ends in .0. No "כ-" before a number: the facts' numbers are exact.

Each written section has a light ("signal"): "green" when the facts it cites lean positive
for the price (price above the averages, a breakout that holds), "red" when they lean negative
(a breakdown that holds, a failed bullish pattern), "yellow" when they are neutral or mixed (a
breakout the price has undone, overbought or oversold, a stretched price). The headline's
light follows the key event. The levels section's light is fixed. A section whose text says
bearish (דובי) cannot be green, one that says bullish (שורי) cannot be red.

"chart" in the brief lists exactly what the reader sees on the chart. Something that is not
drawn gets at most one short sentence.

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
            out.append(f"{key}: {name} {item.get('label', '')} {item['low']}-{item['high']}"
                       + (f", its label also says: {', '.join(notes)}" if notes else ""))
        elif item["type"] == "line":
            out.append(f"{key}: the trendline, labelled with its price today {item['price2']}")
        elif item["type"] == "level":
            out.append(f"{key}: a scenario level line at {item['price']} ({item['label']})")
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
                 dates: set[tuple[int, int, int]], drawn: set[str] | None = None) -> str | None:
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
    jargon = JARGON.findall(text)
    if jargon:
        return (f"jargon {jargon[:2]}: distances in percent, never ATR; always 'מומנטום', never 'תנופה' or "
                "'קו האות'; 'אזור', never 'רצועה'")
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
    lines = {c.split(".")[0] for c in cites if c.startswith("tl_")}
    if drawn is not None and lines - drawn:
        return f"the trendline {sorted(lines - drawn)[0]} is not on the chart: leave it out"
    cited = [facts[c] for c in cites]
    directions = {str(f.get("value")) for c, f in zip(cites, cited) if c.endswith(".direction")}
    if (DOWN_BREAK.search(text) and "דובי" in directions and "שורי" not in directions) or PRICE_BROKEN.search(text):
        return 'a downward break is "שבירה" / "המחיר שבר מתחת ל", never "פרץ" or "נשבר"'
    if LATIN_START.match(text):
        return "starts with Latin letters: start with a Hebrew word"
    if name == "momentum":
        rsi = (facts.get("rsi14") or {}).get("value")
        divergence = any(c.startswith("div_") for c in cites)
        extreme = isinstance(rsi, (int, float)) and not 30 < rsi < 70
        if not divergence and not extreme:
            return "neutral momentum (RSI between 30 and 70, no divergence) adds nothing: leave it out"
        if extreme and not divergence and part.get("signal") in ("green", "red"):
            return "overbought or oversold is a state, not a signal: a yellow light"
    problem = direction_problem(text, cited)
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
    text = YEAR.sub(r"\1", DOLLAR.sub("", " ".join(text.split())))
    text = text.replace("פיבונאצ'י", FIB).replace(" - ", ", ").replace(" (לא תחזית)", "")
    if "יעד" in text and "גובה התבנית" not in text and "כלל המדידה" not in text and FIB not in text:
        text += " (לפי גובה התבנית)"
    return text


def _over(up: bool, number: float) -> str:
    return f"מעל {number:.2f}" if up else f"מתחת ל-{number:.2f}"


def _band_name(what: str, ranged: bool) -> str:
    """A band's name in a sentence: a zone by its kind, a range around the yearly extreme as
    its area (round 3: "the yearly low, between 137.62 and 138.86")."""
    if what in ("ההתנגדות הבאה", "אזור ההתנגדות"):
        return "התנגדות"
    if what in ("התמיכה הבאה", "אזור התמיכה"):
        return "תמיכה"
    return f"אזור {what}" if ranged and what in (HIGH52, LOW52) else what


def _joined(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " ו" + items[-1]


def levels_part(facts: dict[str, dict]) -> list[dict[str, Any]]:
    """The levels line, written by the program from the level map (review round 3: the
    model's levels line and the scenarios named different zones): the nearest resistance
    and support, what else sits in them, a role that flipped, and the distance from the
    close to the near edge."""
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    sentences, cites = [], []
    for kind, word, where in (("r", "התנגדות", "מעל הסגירה"), ("s", "תמיכה", "מתחת לסגירה")):
        key = f"level.{kind}1"
        lo, hi, what = value(f"{key}.low"), value(f"{key}.high"), str(value(f"{key}.what") or "")
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
            sentences.append(f"{'מעל הסגירה אין התנגדות' if kind == 'r' else 'מתחת לסגירה אין תמיכה'} מהשנה האחרונה")
            continue
        cites += [f"{key}.low", f"{key}.high", f"{key}.what"]
        ranged = hi - lo > 1e-9
        place = f"בין {lo:.2f} ל-{hi:.2f}" if ranged else f"{lo:.2f}"
        distance = value(f"{key}.distance_pct")
        zone = what in ("אזור ההתנגדות", "אזור התמיכה")
        if zone and isinstance(distance, (int, float)) and distance == 0:
            text = f"המחיר בתוך אזור ה{word} שבין {lo:.2f} ל-{hi:.2f}"
        elif zone:
            text = f"{word} קרובה {place}"
        else:
            text = f"{word} קרובה: {_band_name(what, ranged)}, {place}"
        includes = value(f"{key}.includes")
        if includes:
            text += f" (כולל {_joined(str(includes).split(', '))})"
            cites.append(f"{key}.includes")
        if isinstance(distance, (int, float)) and distance > 0:
            text += f", {distance:.1f}% {where}"
            cites.append(f"{key}.distance_pct")
        flipped = value(f"{key}.flipped")
        if flipped:
            text += f"; {flipped}"
            cites.append(f"{key}.flipped")
        sentences.append(text)
    if not cites:
        return []
    return [{"part": "levels", "signal": "pin", "text": ". ".join(sentences) + ".", "cites": cites}]


def scenario_parts(facts: dict[str, dict]) -> list[dict[str, Any]]:
    """The two scenarios, written by the program from the up.* / down.* facts: always
    conditional, three short lines each (review round 3: 45-word blocks, and "רמת הכניסה"
    read as a trade entry): what starts it, the next level, what cancels it. Every
    distance names its base: the close for the start, the trigger ("רמת ההפעלה") for the
    next level and the cancel (round 2: the room and the risk were never visible). Right
    after a breakout: the breakout holds while the close stays beyond its line."""
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    out = []
    for side, up in (("up", True), ("down", False)):
        hold, trigger = value(f"{side}.hold"), value(f"{side}.trigger")
        lines, cites = [], []
        if isinstance(hold, (int, float)):
            word, pct = ("הפריצה" if up else "השבירה"), value(f"{side}.hold_pct")
            lines.append(f"{word} נשמרת כל עוד המחיר נסגר {_over(up, hold)}, קו {word} של תבנית "
                         f"{value(f'{side}.hold_pattern')}"
                         + (f" ({pct:.1f}% {'מתחת לסגירה' if up else 'מעל הסגירה'})." if isinstance(pct, (int, float)) else "."))
            cites += [f"{side}.hold", f"{side}.hold_pattern"]
            base = "הסגירה" if up else "לסגירה"
        elif isinstance(trigger, (int, float)):
            what, pct = value(f"{side}.trigger_what"), value(f"{side}.trigger_pct")
            lines.append(f"סגירה {_over(up, trigger)}" + (f", {what}" if what else "")
                         + (f" ({pct:.1f}% {'מעל הסגירה' if up else 'מתחת לסגירה'})." if isinstance(pct, (int, float)) else "."))
            cites += [f"{side}.trigger", f"{side}.trigger_what"]
            base = "רמת ההפעלה" if up else "לרמת ההפעלה"
        elif value(f"{side}.no_next"):
            out.append({"part": side, "signal": side, "text": f"{value(f'{side}.no_next')}.",
                        "cites": [f"{side}.no_next"], "title": PARTS[side]})
            continue
        else:
            continue
        nxt, far = value(f"{side}.next"), value(f"{side}.next_far")
        if isinstance(nxt, (int, float)):
            ranged = isinstance(far, (int, float))
            place = f"בין {min(nxt, far):.2f} ל-{max(nxt, far):.2f}" if ranged else f"{nxt:.2f}"
            room = value(f"{side}.room_pct") if isinstance(trigger, (int, float)) else value(f"{side}.next_pct")
            name = _band_name(str(value(f"{side}.next_what")), ranged)
            measured = name.startswith(("מחושבת", "יעד"))  # a measured level: the number first
            line = (f"הרמה הבאה: {place}" if measured else
                    f"הרמה הבאה: {name}{' ' if name in ('התנגדות', 'תמיכה') else ', '}{place}")
            if isinstance(room, (int, float)):
                line += f" ({room:.1f}% {'מעל ' if up else 'מתחת '}{base})"
            lines.append(line + (f", {name}." if measured else "."))
            cites += [f"{side}.next", f"{side}.next_what"]
        elif value(f"{side}.no_next"):
            lines.append(f"{value(f'{side}.no_next')}.")
            cites.append(f"{side}.no_next")
        cancel, risk = value(f"{side}.cancel"), value(f"{side}.risk_pct")
        if isinstance(hold, (int, float)):
            lines.append(f"סגירה {_over(not up, hold)} מחזירה את המחיר אל תוך התבנית.")
        elif isinstance(cancel, (int, float)):
            what = str(value(f"{side}.cancel_what") or "")
            away = f" ({risk:.1f}% {'מתחת לרמת ההפעלה' if up else 'מעל רמת ההפעלה'})" if isinstance(risk, (int, float)) else ""
            if what == "חזרה אל תוך האזור":
                lines.append(f"ביטול: סגירה חוזרת אל תוך האזור, {_over(not up, cancel)}{away}.")
            elif what == "חצי מהתנודה היומית הממוצעת":     # a one-price trigger: half a day's range back
                lines.append(f"ביטול: סגירה חוזרת {_over(not up, cancel)}, חצי מהתנודה היומית הממוצעת "
                             f"{'מתחת לרמת ההפעלה' if up else 'מעל רמת ההפעלה'}.")
            else:
                lines.append(f"ביטול: סגירה חוזרת {_over(not up, cancel)}" + (f", {what}" if what else "") + f"{away}.")
            cites.append(f"{side}.cancel")
        out.append({"part": side, "signal": side, "text": "\n".join(lines), "cites": cites, "title": PARTS[side]})
    return out


def _numbers_of(text: str, rules: dict[str, Any]) -> set[str]:
    """A text's numbers that say something (the averages' periods and small counts do not:
    round 3 dropped a trend section for repeating "150")."""
    skip = {float(n) for n in rules["ma_periods"]}
    return {n for n in NUMBER.findall(DATE_TEXT.sub(" ", text))
            if not ((x := float(n.replace(",", ""))) in skip or (x.is_integer() and x < 20))}


def check_parts(raw: dict, facts: dict[str, dict], rules: dict[str, Any],
                drawn: set[str] | None = None) -> tuple[dict[str, dict], list[dict]]:
    """The sections that pass (first of each name) and what was dropped, with reasons."""
    numbers, dates = allowed_numbers(facts, rules), fact_dates(facts)
    kept: dict[str, dict] = {}
    dropped: list[dict] = []
    for part in raw.get("parts") or []:
        name = part.get("part")
        optional = sum(1 for n in kept if n not in REQUIRED)
        problem = ("repeated section" if name in kept else
                   "more optional sections than allowed" if name in WRITTEN and name not in REQUIRED
                   and optional >= MAX_OPTIONAL else part_problem(part, facts, numbers, dates, drawn))
        if problem:
            dropped.append({"part": name, "reason": problem, "text": str(part.get("text", ""))[:300]})
            continue
        head = kept.get("headline")
        repeated = (_numbers_of(head["text"], rules) & _numbers_of(str(part.get("text", "")), rules)) if head else set()
        if len(repeated) >= 2 and name not in REQUIRED:
            dropped.append({"part": name, "reason": f"repeats the headline's numbers {sorted(repeated)[:3]}",
                            "text": str(part.get("text", ""))[:300]})
            continue
        kept[name] = {"part": name, "signal": FIXED_LIGHT.get(name, part.get("signal")),
                      "text": _finish(part["text"]),
                      "cites": [c for c in part["cites"] if isinstance(c, str)]}
    return kept, dropped


def write(analysis: Analysis, llm: LLM, *, rules: dict[str, Any] | None = None, name: str | None = None,
          now=lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    """The written analysis: sections in display order, what was dropped, and the usage.
    The model writes the headline and up to two optional sections; the program adds the
    levels line and the two scenarios."""
    rules = rules or load_rules()
    started = time.monotonic()
    system, user, schema = system_prompt(), user_prompt(analysis), response_schema()
    drawn = {k for k, v in simple_view(analysis, rules).items() if v.get("type") == "line"}
    raw, usage = llm.complete(system=system, user=user, schema=schema)
    kept, dropped = check_parts(raw, analysis.facts, rules, drawn)
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
        kept2, dropped2 = check_parts(raw2, analysis.facts, rules, drawn)
        for name, part in kept2.items():
            optional = sum(1 for n in kept if n not in REQUIRED)
            if name in retry and name not in kept and (name in REQUIRED or optional < MAX_OPTIONAL):
                kept[name] = part
        dropped += [{**d, "retry": True} for d in dropped2]
    kept.update({p["part"]: p for p in levels_part(analysis.facts)})
    parts = [{**kept[part], "title": PARTS[part]} for part in PARTS if part in kept]
    parts += scenario_parts(analysis.facts)
    value = lambda key: (analysis.facts.get(key) or {}).get("value")      # noqa: E731
    return {"symbol": analysis.symbol, "name": name, "last_day": analysis.last_day, "parts": parts,
            "close": value("close"), "change_1d_pct": value("change_1d_pct"),
            "omitted": [name for name in REQUIRED if name not in kept],
            "dropped": dropped, "model": getattr(llm, "name", "?"), "usage": usages,
            "seconds": round(time.monotonic() - started, 1),
            "generated_at": now().isoformat(timespec="seconds")}


# ------------------------------------------------------------------ the message
DISCLAIMER = "אין באמור ייעוץ השקעות. כל הרמות מחושבות ממחירי עבר, והן אינן תחזית."


def _day(day: str) -> str:
    y, m, d = day[:10].split("-")
    return f"{d}/{m}/{y}"


TELEGRAM_LIMIT = 4096


def telegram_html(written: dict[str, Any]) -> str:
    """The message (Telegram HTML: every text escaped; only <b> and <i> are ours). Too long
    for one message: optional sections go, last first (never cut in the middle of a tag).
    Review round 3: the company's name in the header, a blank line between sections, and
    only the headline's lead in bold (a whole bold headline was two or three bold lines)."""
    from .chart import _company

    ticker = written["symbol"].split(":")[-1]
    parts = list(written["parts"])
    close, change = written.get("close"), written.get("change_1d_pct")
    quote = ""
    if isinstance(close, (int, float)):
        quote = f"סגירה {close:,.2f} · "
        if isinstance(change, (int, float)):
            quote += f"{'🔺' if change >= 0 else '🔻'} {abs(change):.2f}% · "
    head = f"📊 ניתוח טכני · {ticker}" + (f" · {_company(written['name'])}" if written.get("name") else "")
    while True:
        lines = [f"<b>{html.escape(head)}</b>", html.escape(f"{quote}{_day(written['last_day'])}")]
        for part in parts:
            light = LIGHTS.get(part.get("signal"), "")
            lines.append("")                     # a blank line between sections
            text = html.escape(part["text"])
            if part["part"] == "headline":
                lead, colon, rest = text.partition(":")
                lines.append(f"{light}\u200f <b>{lead}:</b>{rest}" if colon and len(lead) <= 45
                             else f"{light}\u200f <b>{text}</b>")
            else:
                lines.append(f"{light}\u200f <b>{html.escape(part['title'])}:</b> {text}".strip())
        lines.append("")
        if written["omitted"]:
            names = ", ".join(PARTS[n] for n in written["omitted"])
            lines += [html.escape(f"(הושמט: {names}. הטקסט לא עבר את הבדיקה מול הנתונים.)"), ""]
        lines.append(f"<i>{html.escape(DISCLAIMER)}</i>")
        message = "\n".join(lines)
        optional = [p for p in parts if p["part"] not in REQUIRED and p["part"] not in SCENARIOS
                    and p["part"] != "levels"]
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
