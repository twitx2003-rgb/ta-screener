"""Why the market moves (owner, 2026-09-28: "the market is falling in the pre-market and I
don't know why"): the `market-explainer` role reads the index moves, the biggest movers and
the last hours of X news, and writes a few Hebrew sentences.

It runs inside the live watch (live.yml): in each pre-market report, and during the
session when SPY or QQQ moves sharply (`move_due`). The news comes from the X news desk's
state on the PRIVATE state repo's `xnews` branch (`recent`: the raw posts of the last three
hours; `sent_recent`: the items the owner received), fetched fresh through GitHub's API.

Index data (confirmed live, 2026-09-28 11:17 UTC, New York's pre-market): the tool
`mcp-tv-get-symbol-data-batch` takes `symbols` and `columns` and answers
{"data": {symbol: {column: value}}, "missing": [...]}. In the pre-market `close` is the last
regular close and close + premarket_change_abs == premarket_close; during the session
`close` is the live price and `change` its % change from the previous close.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Callable

from .agents import prompt as agent_prompt
from .analyst.text_rules import NUMBER, banned
from .fields import pick
from .premarket import fresh
from .tv.data import fetch_in_session

INDEX_TOOL = "mcp-tv-get-symbol-data-batch"
INDEXES = ("AMEX:SPY", "NASDAQ:QQQ", "AMEX:IWM", "AMEX:DIA")
INDEX_COLUMNS = ["close", "change", "change_abs", "premarket_close", "premarket_change",
                 "premarket_change_abs", "description"]
STATE_REPO = "twitx2003-rgb/ta-screener-state"
POSTS_SHOWN = 120
POST_CHARS = 400
EXPLANATION_MAX = 700
INDEX_NAME_NUMBERS = [500.0, 100.0, 2000.0, 30.0]    # "S&P 500", "Nasdaq 100", "Russell 2000", "Dow 30"


async def fetch_indexes(session, delays) -> dict[str, dict[str, float]]:
    """symbol -> {close, change, premarket_close, premarket_change, premarket_fresh}."""
    payload = await fetch_in_session(session, INDEX_TOOL, {"symbols": list(INDEXES), "columns": INDEX_COLUMNS},
                                     delays=delays)
    data = pick(payload, ["data"], context=INDEX_TOOL)
    out = {}
    for symbol in INDEXES:
        row = data.get(symbol) if isinstance(data, dict) else None
        if not isinstance(row, dict):
            continue
        values = {k: pick(row, [k], context=f"{INDEX_TOOL} {symbol}", allow_null=True)
                  for k in ("close", "change", "premarket_close", "premarket_change", "premarket_change_abs")}
        if values["close"] is None or values["change"] is None:
            continue
        values = {k: (None if v is None else float(v)) for k, v in values.items()}
        values["premarket_fresh"] = (None not in (values["premarket_close"], values["premarket_change_abs"])
                                     and fresh(values["close"], values["premarket_change_abs"],
                                               values["premarket_close"]))
        out[symbol] = values
    return out


def moves(indexes: dict[str, dict], *, premarket: bool) -> dict[str, float]:
    """ticker -> % move now: from the last close in the pre-market (fresh rows only), the
    session's change otherwise."""
    out = {}
    for symbol, v in indexes.items():
        ticker = symbol.split(":")[-1]
        if premarket:
            if v.get("premarket_fresh") and v.get("premarket_change") is not None:
                out[ticker] = round(v["premarket_change"], 2)
        else:
            out[ticker] = round(v["change"], 2)
    return out


def index_line(indexes: dict[str, dict], *, premarket: bool) -> str:
    parts = [f"{t} {m:+.1f}%" for t, m in moves(indexes, premarket=premarket).items()]
    return "📈 " + " · ".join(parts) if parts else ""


def recent_news(now: datetime, *, hours: float = 3, token: str | None = None,
                get: Callable[[str, dict[str, str]], bytes | None] | None = None) -> dict[str, list]:
    """The X news desk's last hours: {"sent": [...], "posts": [...]} (empty without a key)."""
    token = (token if token is not None else os.environ.get("STATE_REPO_TOKEN", "")).strip()
    if not token:
        return {"sent": [], "posts": []}
    raw = (get or _get)(f"https://api.github.com/repos/{STATE_REPO}/contents/state.json?ref=xnews",
                        {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json",
                         "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ta-screener"})
    try:
        state = json.loads(raw or b"{}")
    except ValueError:
        state = {}
    since = now.timestamp() - hours * 3600
    sent = [{"author": r.get("author", ""), "summary_he": r.get("summary_he", ""),
             "minutes_ago": int((now.timestamp() - float(r.get("at", 0))) // 60)}
            for r in state.get("sent_recent") or [] if isinstance(r, dict) and float(r.get("at", 0)) >= since]
    posts = [{"author": r.get("author", ""), "time": r.get("created_at", ""), "text": str(r.get("text", ""))[:POST_CHARS]}
             for r in state.get("recent") or [] if isinstance(r, dict) and float(r.get("added", 0)) >= since]
    return {"sent": sent, "posts": posts[-POSTS_SHOWN:]}


def _get(url: str, headers: dict[str, str]) -> bytes | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
            return response.read()
    except (OSError, ValueError):
        return None


def schema() -> dict:
    return {"type": "object",
            "properties": {"explanation_he": {"type": "string"}, "cause_found": {"type": "boolean"}},
            "required": ["explanation_he", "cause_found"], "additionalProperties": False}


def _numbers(text: str) -> list[float]:
    return [abs(float(n.replace(",", ""))) for n in NUMBER.findall(text)]


def explain(llm, *, moment: str, indexes: dict[str, dict], movers: list[dict],
            news: dict[str, list]) -> str | None:
    """The explanation, or None when the answer breaks a rule (a number not in the input,
    advice or forecast wording) or is empty."""
    premarket = moment == "pre-market"
    facts = {"moment": moment, "indexes": moves(indexes, premarket=premarket),
             "movers": [{"symbol": m["symbol"].split(":")[-1], "name": m.get("name", ""),
                         "move_pct": round(m["change_pct"], 1)} for m in movers],
             "sent_news": news.get("sent", []), "posts": news.get("posts", [])}
    user = json.dumps(facts, ensure_ascii=False, indent=1)
    answer, _ = llm.complete(system=agent_prompt("market-explainer"), user=user, schema=schema())
    text = re.sub(r"\s+", " ", str(answer.get("explanation_he", ""))).strip()
    if not text or banned(text):
        return None
    allowed = _numbers(user) + INDEX_NAME_NUMBERS
    for x in _numbers(text):
        if x.is_integer() and (x < 20 or 1990 <= x <= 2100):   # counts and years: not checked
            continue
        if not any(abs(x - v) <= max(0.005 * v, 0.051) for v in allowed):
            return None
    return text[:EXPLANATION_MAX]


def move_due(now: datetime, indexes: dict[str, dict], history: list[tuple[datetime, dict[str, float]]],
             done: list[dict], *, day_pct: float, hour_pct: float, max_per_day: int,
             min_gap_minutes: float) -> str | None:
    """"day" or "hour" when a sharp session move deserves an explanation now, else None.
    Day: SPY or QQQ at least `day_pct` from the last close (and, after one explanation,
    0.75 points further). Hour: at least `hour_pct` within about an hour. At most
    `max_per_day`, at least `min_gap_minutes` apart."""
    now_moves = {t: m for t, m in moves(indexes, premarket=False).items() if t in ("SPY", "QQQ")}
    if not now_moves or len(done) >= max_per_day:
        return None
    if done and now - datetime.fromisoformat(done[-1]["at"]) < timedelta(minutes=min_gap_minutes):
        return None
    biggest = max(abs(m) for m in now_moves.values())
    last = max((abs(v) for v in done[-1].get("moves", {}).values()), default=0.0) if done else 0.0
    if biggest >= day_pct and biggest >= last + (0.75 if done else 0.0):
        return "day"
    prices = {s.split(":")[-1]: v["close"] for s, v in indexes.items()}
    for at, then in history:
        if timedelta(minutes=50) <= now - at <= timedelta(minutes=75):
            for t in ("SPY", "QQQ"):
                if then.get(t) and prices.get(t) and abs(prices[t] / then[t] - 1) * 100 >= hour_pct:
                    return "hour"
    return None


def move_message(indexes: dict[str, dict], why: str | None) -> str:
    lines = ["🧭 <b>השוק זז חזק</b>", index_line(indexes, premarket=False)]
    lines.append(why if why else "לא מצאתי בחדשות של השעות האחרונות הסבר ברור לתנועה.")
    return "\n".join(line for line in lines if line)
