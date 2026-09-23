"""Writing the channels: the prompt, the checks every post must pass, storage.

One model call per channel per day returns every thread of that channel as JSON
(the schema below), plus one call per live crossing during the session. Nothing
reaches the site unless it passes `post_problem`:

- the persona and the drawings are from the fixed lists;
- the post cites at least one fact key, and every cited key is in the brief;
- every number in the text (and in the chart caption) matches a fact value of
  the thread's brief, within 0.5%; dates, small counts and indicator periods
  (50, 150, 14, ...) are allowed;
- no buy/sell instruction, trading claim or forecast phrase.

A thread must open with its chart post (the recap in #כללי has no chart). A
target always carries "כלל המדידה", and a live crossing "לא סופי עד הסגירה": if
a post leaves them out, they are appended. Everything dropped is recorded with
its reason in the channel's file.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from ..config import ChannelsSettings
from ..errors import ConfigError, ProviderError
from ..llm import LLM, UsageLimit
from ..patterns.rules import Rules
from ..store import Store, symbol_file_stem
from .brief import recap_brief, topic_brief
from .chart_svg import DRAWINGS, render
from .select import GENERAL, Channel, channels, find, pick_topics

log = logging.getLogger(__name__)
PERSONAS_PATH = Path(__file__).with_name("personas.yaml")
MAX_TEXT, MAX_NOTE = 600, 40


# ------------------------------------------------------------------ personas
@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    initials: str
    color: str
    focus: str
    style: str


def load_personas(path: Path = PERSONAS_PATH) -> dict[str, Persona]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = {}
    for key, spec in (raw.get("personas") or {}).items():
        missing = {"name", "initials", "color", "focus", "style"} - set(spec)
        if missing:
            raise ConfigError(f"personas.yaml: {key} is missing {sorted(missing)}")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(spec["color"])):
            raise ConfigError(f"personas.yaml: {key}.color must be #RRGGBB")
        out[key] = Persona(key, str(spec["name"]), str(spec["initials"]), str(spec["color"]),
                           str(spec["focus"]), str(spec["style"]))
    if len(out) < 3:
        raise ConfigError("personas.yaml: a channel needs at least 3 personas")
    return out


# ------------------------------------------------------------------ prompt
RULES = """You write the posts of a simulated channel in a Hebrew stock technical-analysis community.
The website labels every author as an AI agent that simulates a subscriber. Stay in character,
but never claim to be a person, to have bought or sold anything, or to hold a position.

Write everything a reader sees ("text", "note") in Hebrew, in the short, informal style of a
trading chat.

These rules are checked by a program; a post that breaks one is thrown away:
1. Use only the facts in the brief of the topic you are writing about. Each post lists in
   "cites" the fact keys it relies on: at least one, spelled exactly as in the brief.
2. Every number you write must be the value of one of that topic's facts (you may round it to
   fewer decimals). Do not compute new numbers: no differences, sums or percentages of your own.
3. No advice and no forecasts. Never tell anyone to buy, sell, enter, exit or set a stop; never
   say where the price will go. You may say what the book's rules say and which close would
   confirm or cancel the pattern.
4. A target is always "יעד לפי כלל המדידה" (the book's measure rule), never a forecast.
5. If the brief has live.* facts, the crossing happened during the session and is not final
   until the close: say "לא סופי עד הסגירה".
6. Nothing outside the brief: no news, earnings, fundamentals, other stocks, or market talk.

One thread per topic, with that topic's id in "topic":
- Post 0, kind "chart": a persona posts the chart screenshot and explains the setup in 2-4 short
  sentences. "drawings": 1-4 items from the list below, the ones the text talks about. "note":
  a caption of at most 40 characters written on the chart, or "". "reply_to": -1.
- Then 2-3 posts of kind "reply" by other personas: a question, a doubt, a volume or averages
  reading, or an answer to an earlier post. 1-2 sentences each. "drawings": [], "note": "",
  "reply_to": the index of the post it answers.
- Use different personas; each keeps its own focus and style. Call the stock by its ticker.

Drawings (the program draws them from the detection's real geometry):
  pivots = circles on the turning points, with their names; pattern_lines = the pattern's lines;
  confirm_line = the confirmation or neck line; breakout = an arrow at the breakout day;
  target = the measure-rule target; trigger = the level a close must cross next session;
  failure = the level that would cancel the pattern; volume = the volume inside the pattern;
  sma = the 50- and 150-day averages; zone = shade the pattern's range.

A topic with id "recap" (only in #כללי) gets exactly one post of kind "reply" by "bookman",
"reply_to": -1, summarising the day's counts from its brief, with no drawings.

Personas:
"""


def system_prompt(personas: dict[str, Persona]) -> str:
    lines = [f"- {p.id} ({p.name}): looks at {p.focus}. Writes: {p.style}" for p in personas.values()]
    return RULES + "\n".join(lines)


def user_prompt(channel: Channel, rules_he: list[str], briefs: list[dict], *, live: bool) -> str:
    lead = ("LIVE CROSSING during the session. Write one thread: the chart post (its drawings "
            "must include \"trigger\") and one reply." if live
            else f"Write one thread for each of the {len(briefs)} topics.")
    payload = {"channel": channel.name, "pattern_rules": rules_he,
               "topics": [{"topic": b["topic"], "ticker": b["ticker"], "facts": b["facts"]}
                          for b in briefs]}
    return lead + "\n\nBRIEF (JSON):\n" + json.dumps(payload, ensure_ascii=False, indent=1)


def response_schema(persona_ids: list[str], topic_ids: list[str]) -> dict:
    post = {"type": "object", "additionalProperties": False,
            "required": ["persona", "kind", "reply_to", "text", "drawings", "note", "cites"],
            "properties": {"persona": {"type": "string", "enum": persona_ids},
                           "kind": {"type": "string", "enum": ["chart", "reply"]},
                           "reply_to": {"type": "integer"},
                           "text": {"type": "string"},
                           "drawings": {"type": "array", "items": {"type": "string", "enum": list(DRAWINGS)}},
                           "note": {"type": "string"},
                           "cites": {"type": "array", "items": {"type": "string"}}}}
    thread = {"type": "object", "additionalProperties": False, "required": ["topic", "posts"],
              "properties": {"topic": {"type": "string", "enum": topic_ids},
                             "posts": {"type": "array", "items": post}}}
    return {"type": "object", "additionalProperties": False, "required": ["threads"],
            "properties": {"threads": {"type": "array", "items": thread}}}


# ------------------------------------------------------------------ checks
DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}[/.]\d{1,2}(?:[/.]\d{2,4})?\b")
NUMBER = re.compile(r"(?<![\w.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.,])\d+(?:\.\d+)?(?![\w])")
STRUCTURAL = {10.0, 14.0, 20.0, 30.0, 50.0, 52.0, 70.0, 80.0, 100.0, 150.0}
BANNED_WORDS = {"קנו", "תקנו", "מכרו", "תמכרו", "כנסו", "תיכנסו", "היכנסו", "צאו", "סטופ",
                "סטופלוס", "ממליץ", "ממליצה", "ממליצים", "המלצה", "המלצתי", "תשקיעו", "קניתי",
                "מכרתי", "נכנסתי", "שורטתי", "buy", "sell"}
BANNED_PHRASES = ("לקנות עכשיו", "למכור עכשיו", "כדאי לקנות", "כדאי למכור", "בטוח עולה",
                  "בטוח יורד", "בטוח תעלה", "בטוח יעלה", "בטוח תרד", "בטוח ירד", "הולך לעלות",
                  "הולכת לעלות", "הולך לרדת", "הולכת לרדת", "חייב לעלות", "חייבת לעלות",
                  "חייב לרדת", "חייבת לרדת", "stop loss")
PREFIXES = "והשבלכמ"


def fact_numbers(facts: dict[str, dict]) -> list[float]:
    """Every number a post may quote: numeric fact values, and numbers inside text
    facts and labels (thresholds such as '>= 10'), dates excepted."""
    out = []
    for fact in facts.values():
        value = fact.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            out.append(abs(float(value)))
        for text in (value if isinstance(value, str) else "", fact.get("label", "")):
            out += [abs(float(n.replace(",", ""))) for n in NUMBER.findall(DATE.sub(" ", text))]
    return out


def unmatched_numbers(text: str, allowed: list[float]) -> list[str]:
    bad = []
    for raw in NUMBER.findall(DATE.sub(" ", text)):
        x = abs(float(raw.replace(",", "")))
        if (x.is_integer() and x < 20) or x in STRUCTURAL:
            continue
        if not any(abs(x - v) <= max(0.005 * v, 0.051) for v in allowed):
            bad.append(raw)
    return bad


def banned(text: str) -> list[str]:
    """Advice, trading-claim and forecast wording. Hebrew attaches prepositions and
    conjunctions to words ("וקנו"), so up to two such prefix letters are peeled off."""
    low = text.lower()
    found = [p for p in BANNED_PHRASES if p in low]
    for token in re.findall(r"[א-תA-Za-z]+", low):
        forms, word = {token}, token
        for _ in range(2):
            if len(word) > 3 and word[0] in PREFIXES:
                word = word[1:]
                forms.add(word)
        if forms & BANNED_WORDS:
            found.append(token)
    return found


def post_problem(post: dict, facts: dict[str, dict], allowed: list[float],
                 personas: dict[str, Persona]) -> str | None:
    if post.get("persona") not in personas:
        return f"unknown persona {post.get('persona')!r}"
    if post.get("kind") not in ("chart", "reply"):
        return f"unknown kind {post.get('kind')!r}"
    text = " ".join(str(post.get("text", "")).split())
    if not text or len(text) > MAX_TEXT:
        return f"text length {len(text)} (1..{MAX_TEXT})"
    cites = [c for c in post.get("cites") or [] if isinstance(c, str)]
    if not cites:
        return "cites no fact"
    unknown = [c for c in cites if c not in facts]
    if unknown:
        return f"cites unknown fact keys {unknown[:3]}"
    for part in (text, str(post.get("note") or "")):
        numbers = unmatched_numbers(part, allowed)
        if numbers:
            return f"numbers not in the facts: {numbers[:3]}"
        words = banned(part)
        if words:
            return f"advice or forecast wording: {words[:3]}"
    return None


def finish_text(text: str, *, live: bool) -> str:
    text = " ".join(text.split())
    if "יעד" in text and "כלל המדידה" not in text:
        text += " (יעד לפי כלל המדידה של הספר, לא תחזית)"
    if live and "לא סופי" not in text:
        text += " (לא סופי עד הסגירה)"
    return text


def check_thread(raw: dict, brief: dict, personas: dict[str, Persona], *, live: bool,
                 recap: bool) -> tuple[list[dict], list[dict]]:
    """The posts of one thread that pass, and what was dropped (with reasons)."""
    facts = brief["facts"]
    allowed = fact_numbers(facts)
    kept: list[dict] = []
    dropped: list[dict] = []
    new_index: dict[int, int] = {}
    for k, post in enumerate(raw.get("posts") or []):
        problem = post_problem(post, facts, allowed, personas)
        if problem is None and not kept and not recap and post.get("kind") != "chart":
            problem = "a thread must open with its chart post"
        if problem:
            dropped.append({"topic": raw.get("topic"), "post": k, "persona": post.get("persona"),
                            "reason": problem, "text": str(post.get("text", ""))[:200]})
            if not kept:
                break                     # without its opening post the thread is gone
            continue
        chart = post["kind"] == "chart" and not kept and not recap
        drawings = [d for d in post.get("drawings") or [] if d in DRAWINGS] if chart else []
        if chart and live and "trigger" not in drawings:
            drawings.append("trigger")
        reply_to = -1 if not kept else new_index.get(post.get("reply_to"), 0)
        new_index[k] = len(kept)
        kept.append({"persona": post["persona"], "kind": "chart" if chart else "reply",
                     "reply_to": reply_to, "text": finish_text(post["text"], live=live),
                     "drawings": drawings,
                     "note": " ".join(str(post.get("note") or "").split())[:MAX_NOTE] if chart else "",
                     "cites": post["cites"]})
        if recap:
            break                         # the recap is a single post
    return kept, dropped


# ------------------------------------------------------------------ runs
@dataclass
class ChannelWriter:
    store: Store
    rules: Rules
    cfg: ChannelsSettings
    llm: LLM
    personas: dict[str, Persona] = field(default_factory=load_personas)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    # After the subscription's usage limit, no call until then (live mode waits an hour).
    paused_until: datetime | None = None

    def _write_threads(self, day: date, channel: Channel, briefs: list[dict],
                       topics: dict[str, dict], live: dict | None) -> dict[str, Any]:
        spec = {**self.rules.chart, **self.rules.candle}.get(channel.id)
        started = time.monotonic()
        parsed, usage = self.llm.complete(
            system=system_prompt(self.personas),
            user=user_prompt(channel, list(spec.rules_he) if spec else [], briefs, live=live is not None),
            schema=response_schema(sorted(self.personas), [b["topic"] for b in briefs]))
        stamp = self.now()
        by_topic = {b["topic"]: b for b in briefs}
        threads, dropped, used = [], [], set()
        for raw in parsed.get("threads") or []:
            topic = raw.get("topic")
            if topic not in by_topic or topic in used:
                dropped.append({"topic": topic, "reason": "unknown or repeated topic"})
                continue
            used.add(topic)
            brief = by_topic[topic]
            posts, bad = check_thread(raw, brief, self.personas, live=live is not None,
                                      recap=topic == "recap")
            dropped += bad
            if not posts:
                continue
            symbol = brief["symbol"]
            thread_id = f"{day.isoformat()}-{channel.id}-{symbol_file_stem(symbol or 'recap')}"
            if live is not None:
                thread_id += f"-live-{stamp:%H%M%S}"
            if posts[0]["kind"] == "chart":
                bars = self.store.read_bars(symbol)
                if bars is None:
                    dropped.append({"topic": topic, "reason": f"no stored bars for {symbol}"})
                    continue
                svg = render(bars, topics[topic], posts[0]["drawings"], posts[0]["note"],
                             seed=thread_id, title=symbol,
                             live_price=live.get("price") if live else None)
                posts[0]["chart"] = self.store.write_channel_chart(day, thread_id, svg)
            det = topics.get(topic, {})
            threads.append({"id": thread_id, "channel": channel.id, "symbol": symbol,
                            "pattern": det.get("pattern"), "status": det.get("status"),
                            "live": live is not None, "created_at": stamp.isoformat(timespec="seconds"),
                            "posts": [{**p, "id": f"{thread_id}-{n}"} for n, p in enumerate(posts)]})
        return {"channel": channel.id, "name": channel.name, "day": day.isoformat(),
                "generated_at": stamp.isoformat(timespec="seconds"), "model": self.llm.name,
                "usage": usage, "seconds": round(time.monotonic() - started, 1),
                "threads": threads, "dropped": dropped}

    def daily(self, view, *, force: bool = False, only: set[str] | None = None,
              progress: Callable[[str], None] = print) -> dict[str, Any]:
        """One call per channel for the scan's session; a channel already written for
        that session is skipped unless `force`."""
        chans = channels(view.detections, self.rules, self.cfg.count)
        report: dict[str, Any] = {"day": view.day.isoformat(), "channels": {}}
        limited = None
        for ch in chans:
            if only and ch.id not in only:
                continue
            if limited:
                report["channels"][ch.id] = f"skipped: {limited}"
                continue
            if not force and self.store.read_channel_doc(view.day, ch.id) is not None:
                report["channels"][ch.id] = "already written"
                continue
            picked = pick_topics(view.stocks, view.detections, ch.id, self.cfg.topics_per_channel)
            briefs, topics = [], {}
            for k, det in enumerate(picked, 1):
                record = _detection(det)
                briefs.append(topic_brief(view.stock(det["symbol"]), record, f"t{k}"))
                topics[f"t{k}"] = record
            if ch.id == GENERAL:
                briefs.append(recap_brief(chans, view.detections))
            if not briefs:
                report["channels"][ch.id] = "nothing to discuss"
                continue
            progress(f"  {ch.name}: {len(picked)} topic(s)")
            try:
                doc = self._write_threads(view.day, ch, briefs, topics, None)
            except UsageLimit as exc:
                # Every further call would fail the same way; the channels left are
                # written by the next run (it skips the ones already written).
                log.error("%s: %s", ch.name, exc)
                report["channels"][ch.id] = f"failed: {exc}"
                limited = "Claude usage limit; run --channels again after it resets"
                continue
            except ProviderError as exc:
                log.error("%s: %s", ch.name, exc)
                report["channels"][ch.id] = f"failed: {exc}"
                continue
            self.store.write_channel_doc(view.day, ch.id, doc)
            report["channels"][ch.id] = {"threads": len(doc["threads"]),
                                         "posts": sum(len(t["posts"]) for t in doc["threads"]),
                                         "dropped": len(doc["dropped"]), "seconds": doc["seconds"]}
        return report

    def live(self, view, session: date, prices: dict[str, float], changes: dict[str, float],
             crossings: dict[str, list[dict]], *, progress: Callable[[str], None] = print) -> dict[str, Any]:
        """A thread for each new crossing (symbol, pattern, session), at most
        `live_max_per_hour` per hour, in the pattern's channel or #כללי."""
        if self.paused_until is not None and self.now() < self.paused_until:
            return {"session": session.isoformat(), "written": 0, "budget_left": 0,
                    "paused_until": self.paused_until.isoformat(timespec="minutes")}
        doc = self.store.read_channel_doc(session, "live") or {"day": session.isoformat(),
                                                               "threads": [], "posted": [], "dropped": []}
        posted = set(doc["posted"])
        now = self.now()
        recent = [t for t in doc["threads"]
                  if now - datetime.fromisoformat(t["created_at"]) < timedelta(hours=1)]
        budget = self.cfg.live_max_per_hour - len(recent)
        chans = channels(view.detections, self.rules, self.cfg.count)
        written = 0
        for symbol, items in sorted(crossings.items()):
            for cross in items:
                key = f"{symbol}|{cross['pattern']}|{session.isoformat()}"
                if key in posted or budget <= 0:
                    continue
                rows = view.detections.loc[(view.detections["symbol"] == symbol)
                                           & (view.detections["pattern"] == cross["pattern"])
                                           & (view.detections["status"] == "forming")]
                if rows.empty:
                    continue
                record = _detection(rows.iloc[0].to_dict())
                live = {"price": prices.get(symbol), "change_pct": changes.get(symbol),
                        "level": cross["level"], "direction": cross["direction"]}
                channel = find(chans, cross["pattern"]) or chans[0]
                brief = topic_brief(view.stock(symbol), record, "t1", live=live)
                progress(f"  live: {symbol} {cross['pattern']} -> {channel.name}")
                try:
                    one = self._write_threads(session, channel, [brief], {"t1": record}, live)
                except UsageLimit as exc:
                    log.error("live %s: %s; no live posts for an hour", key, exc)
                    self.paused_until = self.now() + timedelta(hours=1)
                    budget = 0
                    continue
                except ProviderError as exc:
                    log.error("live %s: %s", key, exc)
                    continue                     # not marked: tried again next round
                doc["threads"] += one["threads"]
                doc["dropped"] += one["dropped"]
                doc["posted"].append(key)        # even if every post was dropped: no retry loop
                posted.add(key)
                budget -= 1
                written += 1
        if written:
            self.store.write_channel_doc(session, "live", doc)
        return {"session": session.isoformat(), "written": written, "budget_left": max(0, budget)}


def _detection(row: dict[str, Any]) -> dict[str, Any]:
    """A detection row with its JSON columns parsed (what the chart renderer reads)."""
    out = {k: v for k, v in row.items() if not k.endswith("_json")}
    for key in ("points", "lines", "checks"):
        raw = row.get(f"{key}_json")
        out[key] = json.loads(raw) if isinstance(raw, str) and raw else row.get(key) or []
    return out
