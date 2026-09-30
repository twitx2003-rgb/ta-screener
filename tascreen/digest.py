"""The morning digest (owner, 2026-09-29): at 10:00 Israel time, one designed picture in the
owner's group of the news from the last session's open until then: 6-8 short Hebrew
items, the big picture in one sentence, what is due today, and the index funds' moves in
that session.

- When: Tuesday to Saturday from the previous session's open; no digest on Sunday; on
  Monday from Friday's open (the weekend included). A holiday is skipped back over.
- The stories: the X news desk's picks in that time (tascreen/xnews.py keeps every pick,
  sent or held back, in its state's day_log), the most important first.
- The words: the `digest-editor` role (tascreen/agents/), one call. Every number must be
  in the stories or the index moves, and advice or forecast wording drops an item
  (tascreen/research.py text_problem).
- The index moves: the nightly run keeps the last session's (data/market/<day>.json in
  the private state repo); without them the picture has no strip.
- The picture: tascreen/digest_render.py. The run is the X news loop's (it has Claude,
  the news state and the group); `digest.json` beside the news state says it was sent.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .agents import prompt as agent_prompt
from .digest_render import CATEGORIES
from .hebrew import count_he
from .market_hours import is_trading_day, session_bounds

ISRAEL = ZoneInfo("Asia/Jerusalem")
SEND_FROM, SEND_UNTIL = time(10, 0), time(13, 0)   # a late run still sends until 13:00
STORIES_SHOWN = 90
MAX_TRIES = 3                                      # a failing morning is tried at most this often
MIN_ITEMS, MAX_ITEMS = 3, 8
TITLE_MAX, DETAIL_MAX, HEADLINE_MAX, WATCH_MAX = 44, 170, 140, 130
TICKER = re.compile(r"^[A-Z][A-Z0-9.]{0,5}$")
WEEKDAYS = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")
INDEX_NAMES = {"SPY": "S&P 500", "QQQ": "נאסד\"ק 100", "IWM": "ראסל 2000", "DIA": "דאו ג'ונס"}
SYSTEM = agent_prompt("digest-editor")


# ------------------------------------------------------------------ when
def window(now: datetime, market_tz: str) -> tuple[datetime, date] | None:
    """(the open it starts at, that session's day), or None on Sunday."""
    if now.astimezone(ISRAEL).weekday() == 6:
        return None
    day = now.astimezone(ZoneInfo(market_tz)).date() - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(day):
            return session_bounds(day, market_tz)[0], day
        day -= timedelta(days=1)
    return None


def due(now: datetime, record: dict[str, Any], market_tz: str) -> bool:
    local = now.astimezone(ISRAEL)
    today = local.date().isoformat()
    tries = int(record.get("tries", 0)) if record.get("day") == today else 0
    return (SEND_FROM <= local.time() < SEND_UNTIL and record.get("sent") != today and tries < MAX_TRIES
            and window(now, market_tz) is not None)


# ------------------------------------------------------------------ what
def stories(day_log: list[dict], start: datetime, end: datetime) -> list[dict[str, Any]]:
    """The picks in the window, each once, the most important first (then the newest)."""
    lo, hi = start.timestamp(), end.timestamp()
    seen, out = set(), []
    for r in day_log or []:
        if not isinstance(r, dict) or not lo <= float(r.get("at", 0)) <= hi or r.get("id") in seen:
            continue
        seen.add(r.get("id"))
        out.append(r)
    out.sort(key=lambda r: (-int(r.get("importance", 0)), -float(r.get("at", 0))))
    return out[:STORIES_SHOWN]


def schema() -> dict:
    item = {"type": "object", "additionalProperties": False,
            "properties": {"ids": {"type": "array", "items": {"type": "string"}},
                           "category": {"type": "string", "enum": list(CATEGORIES)},
                           "title_he": {"type": "string"}, "detail_he": {"type": "string"},
                           "tickers": {"type": "array", "items": {"type": "string"}}},
            "required": ["ids", "category", "title_he", "detail_he", "tickers"]}
    return {"type": "object", "additionalProperties": False,
            "properties": {"headline_he": {"type": "string"}, "watch_he": {"type": "string"},
                           "items": {"type": "array", "items": item}},
            "required": ["headline_he", "watch_he", "items"]}


def user_prompt(picked: list[dict], moves: dict[str, float]) -> str:
    return json.dumps({
        "index_funds_last_session_pct": moves,
        "stories": [{"id": str(r.get("id")), "time_israel": datetime.fromtimestamp(float(r["at"]), ISRAEL).strftime("%a %H:%M"),
                     "importance": r.get("importance"), "summary_he": r.get("summary_he", ""),
                     "analysis_he": r.get("analysis_he", ""), "account": r.get("author", ""),
                     "accounts_telling_it": 1 + len(r.get("sources") or [])} for r in picked],
    }, ensure_ascii=False, indent=1)


def _allowed(picked: list[dict], moves: dict[str, float]) -> list[float]:
    from .research import _numbers

    text = " ".join(f"{r.get('summary_he', '')} {r.get('analysis_he', '')}" for r in picked)
    from .explain import INDEX_NAME_NUMBERS                 # "S&P 500", "Nasdaq 100", ...

    return (_numbers(text) + [abs(v) for v in moves.values()] + [abs(round(v, 1)) for v in moves.values()]
            + list(INDEX_NAME_NUMBERS))


def check(answer: dict, picked: list[dict], moves: dict[str, float]) -> tuple[dict[str, Any], list[str]]:
    """The items and sentences that pass, and why the others did not."""
    from .research import text_problem

    ids = {str(r.get("id")) for r in picked}
    allowed = _allowed(picked, moves)
    items, dropped = [], []
    for raw in answer.get("items") or []:
        if not isinstance(raw, dict):
            continue
        title, detail = str(raw.get("title_he", "")).strip(), str(raw.get("detail_he", "")).strip()
        used = [str(i) for i in raw.get("ids") or [] if str(i) in ids]
        problem = ("no story of the window" if not used else
                   text_problem(title, allowed) or text_problem(detail, allowed))
        if problem:
            dropped.append(problem)
            continue
        tickers = [t for t in (str(x).strip().lstrip("$").upper() for x in raw.get("tickers") or []) if TICKER.match(t)]
        category = raw.get("category") if raw.get("category") in CATEGORIES else "market"
        items.append({"ids": used, "category": category, "title_he": title[:TITLE_MAX],
                      "detail_he": detail[:DETAIL_MAX], "tickers": tickers[:2]})
        if len(items) >= MAX_ITEMS:
            break
    headline = str(answer.get("headline_he", "")).strip()
    if text_problem(headline, allowed):
        dropped.append("headline: " + str(text_problem(headline, allowed)))
        headline = ""
    watch = str(answer.get("watch_he", "")).strip()
    if watch and text_problem(watch, allowed):
        dropped.append("watch: " + str(text_problem(watch, allowed)))
        watch = ""
    return {"items": items, "headline_he": headline[:HEADLINE_MAX], "watch_he": watch[:WATCH_MAX]}, dropped


def build(now: datetime, day_log: list[dict], moves: dict[str, float], llm, market_tz: str) -> dict[str, Any] | None:
    """The digest to draw, or None when the window has too few stories."""
    found = window(now, market_tz)
    if found is None:
        return None
    opens, session = found
    picked = stories(day_log, opens, now)
    if len(picked) < MIN_ITEMS:
        return None
    user = user_prompt(picked, moves)
    answer, _ = llm.complete(system=SYSTEM, user=user, schema=schema())
    written, dropped = check(answer, picked, moves)
    if len(written["items"]) < MIN_ITEMS or not written["headline_he"]:
        fix = (user + "\n\nYour previous answer was checked by the program and these parts failed: "
               + "; ".join(dropped or ["too few items"]) + ". Write the whole answer again, fixing them.")
        again, _ = llm.complete(system=SYSTEM, user=fix, schema=schema())
        second, dropped2 = check(again, picked, moves)
        if len(second["items"]) >= len(written["items"]):
            written, dropped = second, dropped2
    if len(written["items"]) < MIN_ITEMS:
        return None
    local, opened = now.astimezone(ISRAEL), opens.astimezone(ISRAEL)
    when = "אתמול" if opened.date() == local.date() - timedelta(days=1) else f"ביום {WEEKDAYS[opened.weekday()]}"
    used = {i for item in written["items"] for i in item["ids"]}
    return {
        "kicker": "וול סטריט · סיכום בוקר",
        "title_he": "מה קרה מאז הפתיחה" if when == "אתמול" else f"מה קרה מאז יום {WEEKDAYS[opened.weekday()]}",
        "date_he": f"{WEEKDAYS[local.weekday()]} · {local:%d/%m/%Y}",
        "window_he": f"מפתיחת המסחר {when}, {opened:%H:%M}, עד {local:%H:%M} (שעון ישראל)",
        "indexes": [{"name": INDEX_NAMES.get(k, k), "change": v} for k, v in moves.items() if k in INDEX_NAMES],
        "headline_he": written["headline_he"] or written["items"][0]["title_he"],
        "items": written["items"], "watch_he": written["watch_he"],
        "footer_he": (f"{count_he(len(written['items']), 'ידיעה אחת', 'ידיעות')} מתוך {len(picked)} "
                      f"שנבחרו מ-X · {count_he(len(used), 'מקור אחד', 'מקורות')}"),
        "session": session.isoformat(), "dropped": dropped,
    }


def caption(d: dict[str, Any]) -> str:
    return (f"☀️ <b>סיכום הבוקר של וול סטריט</b> · {html.escape(d['date_he'])}\n"
            f"{html.escape(d['headline_he'])}")


# ------------------------------------------------------------------ the run
def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def run(*, now: datetime, state_path: Path, record_path: Path, market_tz: str, make_llm: Callable[[], Any],
        send_photo: Callable[[bytes, str], None], moves_of: Callable[[date], dict[str, float]],
        draw: Callable[[dict, Path], Path], folder: Path, force: bool = False) -> dict[str, Any]:
    """Send the digest if it is due (or `force`); counts only in the return (public log).
    Each attempt is counted first, so a crash counts too (at most MAX_TRIES a morning)."""
    record = read_json(record_path)
    if not force and not due(now, record, market_tz):
        return {"status": "not due"}
    found = window(now, market_tz)
    if found is None:
        return {"status": "no digest today"}
    today = now.astimezone(ISRAEL).date().isoformat()
    tries = int(record.get("tries", 0)) if record.get("day") == today else 0
    record = {**record, "day": today, "tries": tries + 1}
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record), encoding="utf-8")
    day_log = read_json(state_path).get("day_log") or []
    moves = moves_of(found[1])
    digest = build(now, day_log, moves, make_llm(), market_tz)
    if digest is None:
        return {"status": "too few stories", "stories": len(stories(day_log, found[0], now))}
    png = draw(digest, folder)
    send_photo(png.read_bytes(), caption(digest))
    record = {**record, "sent": today, "at": now.isoformat(timespec="seconds"),
              "items": len(digest["items"]), "dropped": len(digest["dropped"])}
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return {"status": "sent", "items": len(digest["items"]), "dropped": len(digest["dropped"]),
            "indexes": len(digest["indexes"])}
