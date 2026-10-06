"""The setups coach (owner, 2026-10-06: "an expert agent that researches at length, goes over
all the setups daily, sees why setups fell and left the watchlist, works with the technical
analyst agent, and knows where to improve and what to keep").

Every evening, after the setups list's refresh (tascreen/setups_list.py):
1. The ledger (data/setups/ledger.json, private state): every entry the list took in, with its
   setup at entry and the entry close; when it leaves, the day, the reason, the exit price, the
   return, and (when the bars are at hand) the best and worst move while on the list. The
   open's review records its exits too, at the open's price.
2. The study (tascreen/setups_study.py, data/setups/study.json): the same setups over the
   stored history, no look-ahead, and the baseline the list must beat; rebuilt weekly.
3. The coach (agents/setups-coach.md), one model call: why today's exits fell, what to keep and
   what to improve (proposals for the owner, never applied by the program), and short notes
   for the chart analyst (data/setups/analyst_notes.md, added to the analyst's prompt:
   guidance with no numbers, since the analyst quotes only a stock's own facts).
4. The owner's private chat: the coach's short Hebrew note at the end of every trading day
   (owner, 2026-10-06: "the checks agent should work at the end of every day"), and the
   week's note with the proposals after Friday's session.
"""
from __future__ import annotations

import html
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .agents import prompt as agent_prompt
from .patterns.levels import day_of
from .store import _write_json

MAX_ITEMS = 5
REASON_WORDS = {"fell_ma": "נסגרה מתחת לממוצע", "fell_back": "נסגרה מתחת לקו שנפרץ",
                "opened_below": "נפתחה מתחת לרמה", "target": "הגיעה ליעד", "stale": "לא התחדשה",
                "room": "פינוי מקום", "unfollowed": "יצאה מרשימת המניות", "redefined": "הגדרות חדשות"}


# ------------------------------------------------------------------ files
def folder(data_dir: Path) -> Path:
    return data_dir / "setups"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_ledger(data_dir: Path) -> list[dict]:
    rows = _read(folder(data_dir) / "ledger.json", [])
    return rows if isinstance(rows, list) else []


def write_ledger(data_dir: Path, ledger: list[dict]) -> None:
    _write_json(folder(data_dir) / "ledger.json", ledger)


def read_study(data_dir: Path) -> dict[str, Any]:
    study = _read(folder(data_dir) / "study.json", {})
    return study if isinstance(study, dict) else {}


def analyst_notes(data_dir: Path) -> str:
    """The coach's notes for the chart analyst, as a block for its prompt ("" when none)."""
    try:
        text = (folder(data_dir) / "analyst_notes.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ("NOTES FROM THE SETUPS COACH (what held and what failed on the owner's watchlist; "
            "guidance only, never a fact to quote):\n" + text) if text else ""


# ------------------------------------------------------------------ the ledger
def _bar_on(bars: pd.DataFrame | None, day: str) -> int | None:
    if bars is None or bars.empty:
        return None
    days = bars["timestamp"].map(day_of).astype(str)
    hit = days[days == day]
    return int(hit.index[0]) if len(hit) else None


def record(ledger: list[dict], state: dict[str, Any], day: str, *,
           price_of: Callable[[str], float | None], bars_of: Callable[[str], pd.DataFrame | None] | None = None) -> list[dict]:
    """The ledger after one refresh (or the open's review): an entry opens a record, a renewal
    counts, an exit closes it with its return (from the entry price to `price_of`) and, with
    `bars_of`, its best and worst move and the sessions it stayed."""
    ledger = [dict(r) for r in ledger]
    open_by = {r["symbol"]: r for r in ledger if r.get("open")}
    entries = {e["symbol"]: e for e in state.get("entries") or []}
    for ev in state.get("events") or []:
        symbol, what = ev["symbol"], ev["what"]
        if what in ("added", "filled") and symbol not in open_by:
            e = entries.get(symbol, {})
            price = price_of(symbol)
            rec = {"symbol": symbol, "open": True, "added": day, "kind": ev.get("kind"),
                   "pattern": e.get("pattern"), "text": ev.get("text") or e.get("text"),
                   "volume": e.get("volume"), "strict": e.get("strict", True), "filled": what == "filled",
                   "from_lists": bool(e.get("from_lists")), "since": e.get("since"),
                   "entry": round(float(price), 4) if _finite(price) else None,
                   "level": round(float(e["watch"]), 4) if _finite(e.get("watch")) else None, "renewed": 0}
            ledger.append(rec)
            open_by[symbol] = rec
        elif what == "renewed" and symbol in open_by:
            rec = open_by[symbol]
            rec["renewed"] = int(rec.get("renewed") or 0) + 1
            rec["kind_now"] = ev.get("kind")
        elif what == "removed" and symbol in open_by:
            rec = open_by.pop(symbol)
            price = price_of(symbol)
            rec.update(open=False, left=day, reason=ev.get("reason"),
                       exit=round(float(price), 4) if _finite(price) else None)
            if _finite(rec.get("entry")) and _finite(price):
                rec["ret_pct"] = round((float(price) / float(rec["entry"]) - 1) * 100, 2)
            bars = bars_of(symbol) if bars_of else None
            a, b = _bar_on(bars, rec["added"]), _bar_on(bars, day)
            if a is not None and b is not None and b > a and _finite(rec.get("entry")):
                span = bars.iloc[a + 1:b + 1]
                rec["sessions"] = b - a
                rec["mfe_pct"] = round((float(span["high"].max()) / float(rec["entry"]) - 1) * 100, 2)
                rec["mae_pct"] = round((float(span["low"].min()) / float(rec["entry"]) - 1) * 100, 2)
    return ledger


def _finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def record_summary(ledger: list[dict]) -> dict[str, Any]:
    """The decided records by kind and by reason: counts, the share up, the average return."""
    done = [r for r in ledger if not r.get("open")]

    def stats(rows: list[dict]) -> dict[str, Any]:
        rets = [r["ret_pct"] for r in rows if _finite(r.get("ret_pct"))]
        return {"n": len(rows), "up_pct": round(sum(x > 0 for x in rets) / len(rets) * 100, 1) if rets else None,
                "ret_avg": round(sum(rets) / len(rets), 2) if rets else None}

    kinds = sorted({str(r.get("kind")) for r in done})
    reasons = sorted({str(r.get("reason")) for r in done})
    return {"decided": len(done), "open": len(ledger) - len(done), "all": stats(done),
            "by_kind": {k: stats([r for r in done if str(r.get("kind")) == k]) for k in kinds},
            "by_reason": {k: stats([r for r in done if str(r.get("reason")) == k]) for k in reasons},
            "filled_vs_strict": {"strict": stats([r for r in done if not r.get("filled")]),
                                 "filled": stats([r for r in done if r.get("filled")])}}


# ------------------------------------------------------------------ the coach
def schema() -> dict:
    why = {"type": "object", "additionalProperties": False, "required": ["symbol", "text"],
           "properties": {"symbol": {"type": "string"}, "text": {"type": "string"}}}
    strings = {"type": "array", "items": {"type": "string"}}
    return {"type": "object", "additionalProperties": False,
            "required": ["why_fell", "keep", "improve", "analyst_notes", "summary_he", "why_he", "improve_he"],
            "properties": {"why_fell": {"type": "array", "items": why}, "keep": strings, "improve": strings,
                           "analyst_notes": strings, "summary_he": {"type": "string"},
                           "why_he": {"type": "array", "items": why}, "improve_he": strings}}


def inputs(state: dict[str, Any], ledger: list[dict], study: dict[str, Any], settings: dict[str, Any],
           lessons: str, day: str) -> dict[str, Any]:
    """What the coach reads tonight (no secrets: the list's own records and the study)."""
    left = [r for r in ledger if not r.get("open") and r.get("left") == day]
    return {"day": day, "today": state.get("events") or [], "left_today": left,
            "on_the_list": [{k: e.get(k) for k in ("symbol", "kind", "since", "volume", "filled", "from_lists")}
                            for e in state.get("entries") or []],
            "record": record_summary(ledger), "study": study, "settings": settings,
            "lessons": lessons or "none yet"}


def _clean(items: Any, limit: int = MAX_ITEMS) -> list[str]:
    return [re.sub(r"\s+", " ", str(x)).strip() for x in items or [] if str(x).strip()][:limit]


def coach(llm, user: dict[str, Any]) -> dict[str, Any]:
    """One model call. Hebrew text whose numbers are not in the input is dropped
    (research.text_problem); notes for the analyst carry no digits at all."""
    from .research import _numbers, text_problem

    text = json.dumps(user, ensure_ascii=False, default=str)
    answer, usage = llm.complete(system=agent_prompt("setups-coach"), user=text, schema=schema())
    allowed = _numbers(text)
    summary = str(answer.get("summary_he", "")).strip()
    if text_problem(summary, allowed):
        summary = ""
    why_he = [w for w in answer.get("why_he") or []
              if isinstance(w, dict) and w.get("text") and not text_problem(str(w["text"]), allowed)]
    notes = [n for n in _clean(answer.get("analyst_notes")) if not re.search(r"\d", n)]
    improve_he = [x for x in _clean(answer.get("improve_he")) if not text_problem(x, allowed)]
    return {"why_fell": [w for w in answer.get("why_fell") or [] if isinstance(w, dict)][:30],
            "keep": _clean(answer.get("keep")), "improve": _clean(answer.get("improve")),
            "analyst_notes": notes, "summary_he": summary, "why_he": why_he[:30], "improve_he": improve_he,
            "usage": usage}


def save(data_dir: Path, day: str, result: dict[str, Any]) -> None:
    """The night's answer (data/setups/coach/<day>.json), the lessons the coach reads next time,
    and the analyst's notes."""
    base = folder(data_dir)
    _write_json(base / "coach" / f"{day}.json", {k: v for k, v in result.items() if k != "usage"})
    lessons = ["Keep: " + x for x in result["keep"]] + ["Improve: " + x for x in result["improve"]]
    if lessons:
        (base / "lessons.md").write_text("\n".join(f"- {x}" for x in lessons) + "\n", encoding="utf-8")
    if result["analyst_notes"]:
        (base / "analyst_notes.md").write_text("\n".join(f"- {x}" for x in result["analyst_notes"]) + "\n",
                                               encoding="utf-8")


def read_lessons(data_dir: Path) -> str:
    try:
        return (folder(data_dir) / "lessons.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def message(result: dict[str, Any], left: list[dict], *, weekly: bool = False,
            on_list: int | None = None, record: dict[str, Any] | None = None) -> str:
    """The owner's private note at the end of every trading day: the coach's summary, why each
    exit fell, and the program's own line on the list and its record; after Friday's session
    the week's note with the proposals."""
    lines = ["🧠 <b>מאמן הסטאפים · " + ("סיכום שבועי" if weekly else "סיכום יום") + "</b>"]
    if result.get("summary_he"):
        lines += ["", html.escape(result["summary_he"])]
    why = {w["symbol"]: w["text"] for w in result.get("why_he") or []}
    if left:
        lines += ["", "<b>למה יצאו היום:</b>"]
        for r in left:
            ticker = html.escape(str(r["symbol"]).split(":")[-1])
            reason = REASON_WORDS.get(str(r.get("reason")), str(r.get("reason")))
            ret = f" ({r['ret_pct']:+.1f}%)" if _finite(r.get("ret_pct")) else ""
            lines.append(f"• {ticker}: {reason}{ret}" + (f". {html.escape(why[r['symbol']])}" if r["symbol"] in why else ""))
    if weekly and result.get("improve_he"):
        lines += ["", "<b>הצעות לשיפור (לאישורך, הבוט לא משנה לבד):</b>"]
        lines += [f"• {html.escape(x)}" for x in result["improve_he"]]
    facts = []                                     # the program's words: never the model's numbers
    if on_list is not None:
        facts.append(f"ברשימה עכשיו {on_list} מניות")
    if record and record.get("decided"):
        part = record.get("all") or {}
        up = f", {part['up_pct']:.0f}% מהן ברווח ביציאה" if _finite(part.get("up_pct")) else ""
        facts.append(f"הוכרעו עד היום {record['decided']}{up}")
    elif record is not None:
        facts.append("עוד אין מניות שיצאו מאז שהיומן נפתח")
    if facts:
        lines += ["", html.escape("; ".join(facts)) + "."]
    return "\n".join(lines)
