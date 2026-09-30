"""The breakout research team (owner, 2026-09-28): instead of every breakout, the evening
report brings only THE BEST setups of the day, chosen by a team of agent roles
(tascreen/agents/): three specialists and a chief strategist, who learn weekly from how
their past picks ended.

1. Candidates: the session's confirmed bullish breakouts and the forming patterns on the
   verge of one (alerts.bullish_breakouts, alerts.on_the_verge).
2. Shortlist (code, `score`): at most `shortlist` by a transparent score.
3. A dossier per shortlisted stock (code): the analyst engine's facts, the scan's row, the
   detection and its checklist, relative strength against all stocks and its sector, days
   to earnings, the headline, and what similar breakouts did in the outcome ledger.
4. `pattern-auditor`, `context-analyst`, `statistician`: one call each over the shortlist.
5. `chief-strategist`: at most `max_picks`, or none; Hebrew reasons. A pick whose text has
   a number that is not in its dossier or verdicts, or advice wording, is dropped.
6. Picks are recorded (data/research/picks.json); `weekly_review` compares them with the
   outcome ledger, and `learning-coach` rewrites the lessons (data/research/lessons.md)
   that the team reads from then on.
"""
from __future__ import annotations

import html
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from . import alerts
from .agents import prompt as agent_prompt
from .analyst.text_rules import NUMBER, STRUCTURAL, banned
from .errors import ProviderError
from .outcomes import FINAL
from .patterns.levels import detection_key
from .store import Store, _write_json
from .web.data import ScanView, detection_record

SPECIALISTS = ("pattern-auditor", "context-analyst", "statistician")
EARNINGS_SOON_DAYS = 7


# ------------------------------------------------------------------ candidates and shortlist
def _finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _num(x: Any, digits: int = 2) -> float | None:
    return round(float(x), digits) if _finite(x) else None


def candidates(view: ScanView, breakouts: list[dict], verge: list[dict]) -> list[dict[str, Any]]:
    """The evening's breakouts and verge candidates in one list, each with its detection."""
    d = view.detections
    out = [{**b, "kind": "breakout"} for b in breakouts]
    for v in verge:
        rows = d.loc[(d["symbol"] == v["symbol"]) & (d["pattern"] == v["pattern"]) & (d["status"] == "forming")]
        if len(rows):
            out.append({**v, "kind": "verge", "record": detection_record(rows.iloc[0].to_dict())})
    return out


def _days_to(next_earnings: Any, day: date) -> int | None:
    try:
        when = pd.Timestamp(next_earnings)
    except (TypeError, ValueError):
        return None
    if pd.isna(when):
        return None
    return (when.date() - day).days


def score(c: dict[str, Any], stock: dict[str, Any], day: date, verge_pct: float) -> float:
    """The shortlist's score: a few plain points, so the shortlist can be explained."""
    s = (c.get("hit_rate") if c.get("hit_rate") is not None else 35.0) / 10
    s += 1.0 if stock.get("above_sma50") else 0.0
    s += 1.0 if stock.get("above_sma150") else 0.0
    if _finite(stock.get("pct_from_52w_high")) and float(stock["pct_from_52w_high"]) > -10:
        s += 1.0
    if _finite(stock.get("rel_volume")):
        s += min(float(stock["rel_volume"]), 3.0) * (2.0 if c["kind"] == "breakout" else 1.0)
    days = _days_to(stock.get("next_earnings"), day)
    if days is not None and 0 <= days <= EARNINGS_SOON_DAYS:
        s -= 3.0
    if c["kind"] == "breakout" and _finite(c.get("close")) and _finite(c.get("breakout")) and float(c["breakout"]) > 0:
        if (float(c["close"]) / float(c["breakout"]) - 1) * 100 > 6:
            s -= 2.0                            # already ran far past the line
    if c["kind"] == "verge" and _finite(c.get("gap_pct")):
        s += max(0.0, verge_pct - float(c["gap_pct"]))
    return round(s, 2)


SHORTLIST_PER_PATTERN = 4    # variety (owner, 2026-09-28): one pattern never fills the shortlist
PICKS_PER_PATTERN = 2        # ...nor the picks (it was 1 of 3; owner, 2026-09-30: up to ten picks)


def shortlist(cands: list[dict], stocks: dict[str, dict], day: date, verge_pct: float, n: int,
              per_pattern: int = SHORTLIST_PER_PATTERN) -> list[dict]:
    """The `n` best scored candidates, at most `per_pattern` of one pattern."""
    scored = [{**c, "score": score(c, stocks.get(c["symbol"], {}), day, verge_pct)} for c in cands]
    scored.sort(key=lambda c: (-c["score"], c["symbol"]))
    out: list[dict] = []
    per: dict[str, int] = {}
    for c in scored:
        if per.get(c["pattern"], 0) < per_pattern:
            per[c["pattern"]] = per.get(c["pattern"], 0) + 1
            out.append(c)
    return out[:n]


# ------------------------------------------------------------------ dossiers
def history(ledger: pd.DataFrame | None, pattern: str, reward_pct: float | None) -> dict[str, Any]:
    """What the pattern's decided breakouts did in our ledger, overall and at a similar
    target distance."""
    if ledger is None or ledger.empty:
        return {"decided": 0}
    g = ledger.loc[(ledger["pattern"] == pattern) & ledger["outcome"].isin(FINAL)]

    def stats(rows: pd.DataFrame) -> dict[str, Any]:
        n = len(rows)
        if not n:
            return {"decided": 0}
        return {"decided": n, "target_pct": round((rows["outcome"] == "target").mean() * 100, 1),
                "failed_pct": round((rows["outcome"] == "failed").mean() * 100, 1),
                "median_mfe_pct": _num(rows["mfe_pct"].median(), 1),
                "median_mae_pct": _num(rows["mae_pct"].median(), 1),
                "median_sessions": _num(rows["sessions"].median(), 0)}

    out = stats(g)
    if reward_pct is not None and len(g):
        dist = g["height"] / g["breakout_price"] * 100
        near = g.loc[(dist - reward_pct).abs() <= max(3.0, 0.3 * reward_pct)]
        out["similar"] = {"target_distance_pct": round(reward_pct, 1), **stats(near)}
    return out


def _compact_facts(analysis) -> dict[str, Any]:
    return {k: v.get("value") for k, v in (analysis.facts or {}).items() if isinstance(v, dict)}


def dossier(c: dict[str, Any], view: ScanView, stocks: dict[str, dict], ledger: pd.DataFrame | None,
            backtest: dict | None, bars: pd.DataFrame | None, market: dict[str, float],
            headline: dict | None, analyse: Callable | None = None) -> dict[str, Any]:
    stock = stocks.get(c["symbol"], {})
    rec = c.get("record") or {}
    close = float(c.get("close")) if _finite(c.get("close")) else _num(stock.get("close"))
    line = c.get("breakout") if c["kind"] == "breakout" else c.get("line")
    target = c.get("target")
    inval = c.get("invalidation")
    if not _finite(inval) and rec:
        inval = alerts.invalidation(rec, bars)
    reward = (float(target) / float(close) - 1) * 100 if _finite(target) and close else None
    risk = (1 - float(inval) / float(close)) * 100 if _finite(inval) and close else None
    all20 = pd.to_numeric(view.stocks["change_20d_pct"], errors="coerce")
    sector = view.stocks.loc[view.stocks["sector"] == stock.get("sector"), "change_20d_pct"]
    try:
        checks = json.loads(rec.get("checks_json") or "[]")
    except (TypeError, ValueError):
        checks = []
    out = {
        "symbol": c["symbol"], "name": str(stock.get("description") or ""), "kind": c["kind"],
        "pattern": c["pattern"], "pattern_he": c.get("name"),
        "shortlist_score": c.get("score"),
        "pattern_detail": {"line": _num(line), "close": _num(close), "target": _num(target),
                           "invalidation": _num(inval), "reward_pct": _num(reward, 1), "risk_pct": _num(risk, 1),
                           "extended_pct": _num((close / float(line) - 1) * 100, 1)
                           if c["kind"] == "breakout" and _finite(line) and close else None,
                           "gap_below_line_pct": _num(c.get("gap_pct"), 1),
                           "start": str(rec.get("start", ""))[:10], "end": str(rec.get("end", ""))[:10],
                           "volume_trend": rec.get("volume_trend"), "checks": checks},
        "stock": {k: (_num(stock.get(k)) if k not in ("above_sma50", "above_sma150", "sector", "industry")
                      else stock.get(k))
                  for k in ("change_1d_pct", "change_5d_pct", "change_20d_pct", "above_sma50", "above_sma150",
                            "sma50_slope_10d_pct", "rsi14", "pct_from_52w_high", "pct_from_52w_low",
                            "rel_volume", "atr_pct", "sector", "industry")},
        "rs_20d_pctile": _num((all20 < float(stock["change_20d_pct"])).mean() * 100, 0)
        if _finite(stock.get("change_20d_pct")) else None,
        "sector_20d_median_pct": _num(pd.to_numeric(sector, errors="coerce").median(), 1),
        "days_to_earnings": _days_to(stock.get("next_earnings"), view.day),
        "market": market,
        "news": ({"title": headline["title"], "source": headline.get("provider", ""),
                  "hours_ago": _num(headline.get("hours"), 0)} if headline else None),
        "history": history(ledger, c["pattern"], reward),
        "backtest": next((r for r in (backtest or {}).get("rows", [])
                          if r.get("pattern") == c["pattern"] and r.get("direction") == "bullish"), None),
    }
    if analyse is not None and bars is not None and len(bars) > 60:
        try:
            out["facts"] = _compact_facts(analyse(bars, c["symbol"]))
        except Exception as exc:                    # the engine's facts are a bonus, never a stop
            out["facts_error"] = type(exc).__name__
    return out


# ------------------------------------------------------------------ the team
def _verdict_schema() -> dict:
    return {"type": "object", "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {"symbol": {"type": "string"}, "score": {"type": "integer", "minimum": 1, "maximum": 10},
                       "for": {"type": "array", "items": {"type": "string"}},
                       "against": {"type": "array", "items": {"type": "string"}}},
        "required": ["symbol", "score", "for", "against"], "additionalProperties": False}}},
            "required": ["verdicts"], "additionalProperties": False}


def _chief_schema() -> dict:
    return {"type": "object", "properties": {"picks": {"type": "array", "items": {
        "type": "object",
        "properties": {"symbol": {"type": "string"}, "conviction": {"type": "integer", "minimum": 1, "maximum": 10},
                       "why_he": {"type": "string"}, "cancels_he": {"type": "string"},
                       "watch_he": {"type": "string"}},
        "required": ["symbol", "conviction", "why_he", "cancels_he", "watch_he"], "additionalProperties": False}}},
            "required": ["picks"], "additionalProperties": False}


def _dumps(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


def _numbers(text: str) -> list[float]:
    return [abs(float(n.replace(",", ""))) for n in NUMBER.findall(text)]


def text_problem(text: str, allowed: list[float]) -> str | None:
    """Why a Hebrew text may not go out: advice wording, or a number not in its sources."""
    if not text.strip():
        return "empty"
    if banned(text):
        return "advice or forecast wording"
    for x in _numbers(text):
        if (x.is_integer() and (x < 20 or 1990 <= x <= 2100)) or x in STRUCTURAL:
            continue
        if not any(abs(x - v) <= max(0.005 * v, 0.051) for v in allowed):
            return f"number {x:g} not in the sources"
    return None


def run_team(dossiers: list[dict], llm, lessons: str = "", max_picks: int = 3) -> dict[str, Any]:
    """The specialists' verdicts and the chief's checked picks."""
    shortlist_symbols = {d["symbol"] for d in dossiers}
    base = {"lessons": lessons or "none yet", "dossiers": dossiers}
    verdicts: dict[str, dict[str, dict]] = {}
    for role in SPECIALISTS:
        answer, _ = llm.complete(system=agent_prompt(role), user=_dumps(base), schema=_verdict_schema())
        verdicts[role] = {v["symbol"]: v for v in answer.get("verdicts") or []
                          if isinstance(v, dict) and v.get("symbol") in shortlist_symbols}
    by_symbol = {s: {role: verdicts[role].get(s) for role in SPECIALISTS} for s in shortlist_symbols}
    answer, _ = llm.complete(system=agent_prompt("chief-strategist"),
                             user=_dumps({**base, "verdicts": by_symbol}), schema=_chief_schema())
    picks, dropped = [], []
    dossier_of = {d["symbol"]: d for d in dossiers}
    per_pattern: dict[str, int] = {}
    for p in answer.get("picks") or []:
        if not isinstance(p, dict) or p.get("symbol") not in shortlist_symbols:
            dropped.append("not in the shortlist")
            continue
        pattern = dossier_of[p["symbol"]]["pattern"]
        if any(q["symbol"] == p["symbol"] for q in picks) or per_pattern.get(pattern, 0) >= PICKS_PER_PATTERN:
            continue                            # variety: two picks per pattern, the chief's first
        allowed = _numbers(_dumps(dossier_of[p["symbol"]]) + _dumps(by_symbol[p["symbol"]]))
        problems = [text_problem(str(p.get(k, "")), allowed) for k in ("why_he", "cancels_he", "watch_he")]
        if any(problems):
            dropped.append(next(x for x in problems if x))
            continue
        per_pattern[pattern] = per_pattern.get(pattern, 0) + 1
        picks.append({"symbol": p["symbol"], "conviction": int(p.get("conviction") or 0),
                      "why_he": p["why_he"].strip(), "cancels_he": p["cancels_he"].strip(),
                      "watch_he": p["watch_he"].strip(),
                      "scores": {role: (by_symbol[p["symbol"]][role] or {}).get("score") for role in SPECIALISTS}})
        if len(picks) >= max_picks:
            break
    return {"picks": picks, "dropped": dropped, "verdicts": by_symbol,
            "chief_picked": len(answer.get("picks") or [])}


def research(view: ScanView, store: Store, breakouts: list[dict], verge: list[dict], *, llm, verge_pct: float,
             shortlist_size: int, max_picks: int, news_of: Callable[[list[str]], dict[str, dict]] | None = None,
             market: dict[str, float] | None = None, analyse: Callable | None = None,
             patterns: set[str] | None = None) -> dict[str, Any]:
    """The whole evening: candidates -> shortlist -> dossiers -> team. Only `patterns` (the
    proven ones, alerts.proven_patterns; None: all). status: "picked", "none" (the team
    found nothing good enough) or "failed" (every pick broke a rule)."""
    stocks = {r["symbol"]: r for r in view.stocks.to_dict("records")}
    every = candidates(view, breakouts, verge)
    cands = [c for c in every if patterns is None or c["pattern"] in patterns]
    out: dict[str, Any] = {"candidates": len(cands), "unproven": len(every) - len(cands),
                           "breakouts": len(breakouts), "verge": len(verge),
                           "shortlist": [], "picks": [], "dropped": []}
    if not cands:
        return {**out, "status": "none"}
    short = shortlist(cands, stocks, view.day, verge_pct, shortlist_size)
    ledger, backtest = store.read_ledger(), store.read_backtest()
    news = news_of(sorted({c["symbol"] for c in short})) if news_of else {}
    dossiers = [dossier(c, view, stocks, ledger, backtest, store.read_bars(c["symbol"]), market or {},
                        news.get(c["symbol"]), analyse) for c in short]
    team = run_team(dossiers, llm, read_lessons(store), max_picks)
    by = {c["symbol"]: c for c in short}
    picks = [{**p, "candidate": by[p["symbol"]]} for p in team["picks"]]
    status = "picked" if picks else ("failed" if team["chief_picked"] and team["dropped"] else "none")
    return {**out, "status": status, "shortlist": [c["symbol"] for c in short], "picks": picks,
            "dropped": team["dropped"], "verdicts": team["verdicts"]}


# ------------------------------------------------------------------ the message
def pick_caption(n: int, p: dict[str, Any]) -> str:
    c = p["candidate"]
    if c["kind"] == "breakout":
        where = f"פריצה ב-{alerts._price(c.get('breakout'))} · סגירה {alerts._price(c.get('close'))}"
    else:
        where = (f"על סף פריצה: קו {alerts._price(c.get('line'))} · סגירה {alerts._price(c.get('close'))} "
                 f"({float(c.get('gap_pct', 0)):.1f}% מתחת)")
    return (f"🏆 <b>{n}.</b> {alerts._link(c['symbol'])} · {html.escape(str(c.get('name', '')))}\n{where}\n"
            f"✅ {html.escape(p['why_he'])}\n❌ {html.escape(p['cancels_he'])}\n👀 {html.escape(p['watch_he'])}")


def report_messages(day: date, result: dict[str, Any], *, intraday: list[dict] | None = None,
                    live_summary: dict | None = None, analyses: list[str] | None = None,
                    with_photo: set[int] | None = None) -> list[str]:
    """The evening report's text. Picks numbered in `with_photo` go as photos with their
    caption instead (evening_report)."""
    n, k = result["candidates"], len(result["picks"])
    head = (f"<b>🏆 המובחרות של {alerts._day(day)}</b>\n"
            f"צוות המחקר בדק {n} מועמדות ({result['breakouts']} פריצות, {result['verge']} על סף פריצה) "
            + (f"ובחר {k}." if k else "ולא מצא היום אף אחת שעומדת ברף."))
    blocks = [head]
    blocks += [pick_caption(i, p) for i, p in enumerate(result["picks"], 1) if i not in (with_photo or ())]
    if analyses:
        blocks.append("📊 ניתוח מלא יגיע בהודעות נפרדות: "
                      + ", ".join(html.escape(s.split(":")[-1]) for s in analyses))
    if intraday:
        held = sum(f["held"] for f in intraday)
        blocks.append(f"⚡ חציות מהמסחר היום: {held} החזיקו מעל הקו בסגירה, {len(intraday) - held} חזרו מתחתיו.")
    if live_summary and live_summary.get("passes"):
        blocks.append(f"🔎 מעקב המסחר היום: {live_summary.get('alerts', 0)} התראות חצייה.")
    blocks.append(f"<i>{html.escape(alerts.DISCLAIMER)}</i>")
    return alerts._pack(blocks)


CAPTION_MAX = 1000
RESEARCH_FAILED_NOTE = "⚠️ צוות המחקר לא הצליח לרוץ היום, אז זה הדיווח הרגיל עם כל הפריצות."


def evening_report(store: Store, view: ScanView, cfg, *, bot: Any, min_cases: int,
                   run: Callable[[list[dict], list[dict]], dict[str, Any]],
                   dispatch: Callable[[str, dict[str, str]], int], can_dispatch: bool,
                   fallback: Callable[[str], dict[str, Any]],
                   now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                   live_summary: dict[str, Any] | None = None,
                   to_png: Callable[[str], bytes] | None = None) -> dict[str, Any]:
    """The research team's evening report, once per session: only the picks (each as its
    pattern chart with the team's reasons), the full analyses of the picks, and one line
    on the day's crossings. If the research fails, `fallback(note)` sends the regular
    report with a note, so a day is never lost. Counts only in the return (public log)."""
    sent = alerts.read_sent(store, view.day)
    if sent.get("evening"):
        return {"status": "already sent"}
    rates = alerts.hit_rates(store.read_ledger(), min_cases)
    breakouts = alerts.bullish_breakouts(view, store.read_bars, rates)
    # the setups: forming patterns up to watch_pct below their line (owner, 2026-09-30: "a
    # really good setup"; verge_pct alone gave one candidate)
    verge = [{**v, "hit_rate": rates.get(v["pattern"])} for v in alerts.on_the_verge(view, cfg.watch_pct)]
    try:
        result = run(breakouts, verge)
    except Exception as exc:                        # UsageLimit, ProviderError, a bug: never a lost day
        import logging
        logging.getLogger(__name__).error("research failed: %s", exc)
        result = {"status": "failed", "error": type(exc).__name__}
    if result.get("status") not in ("picked", "none"):
        report = fallback(RESEARCH_FAILED_NOTE)
        return {**report, "research": result.get("error") or result.get("status")}
    picks = result["picks"]
    photos, with_photo = [], set()
    for i, p in enumerate(picks, 1):
        caption = pick_caption(i, p)
        c = p["candidate"]
        shot = alerts._photo(store.read_bars(c["symbol"]), c.get("record"), caption,
                             to_png or alerts.default_png) if len(caption) <= CAPTION_MAX else None
        if shot:
            photos.append(shot)
            with_photo.add(i)
    chosen = [p["candidate"]["symbol"] for p in picks][:cfg.top_analyses] if can_dispatch else []
    messages = report_messages(view.day, result, intraday=alerts.intraday_followup(view, sent.get("live") or {}),
                               live_summary=live_summary, analyses=chosen, with_photo=with_photo)
    for message in messages:
        bot.send(message, html=True)
    if photos:
        try:
            bot.send_album(photos)
        except (ProviderError, OSError):            # the pictures failed: their text still goes
            for i in sorted(with_photo):
                bot.send(pick_caption(i, picks[i - 1]), html=True)
    started = [s for s in chosen if dispatch("analyst.yml", {"symbol": s}) == 204]
    record_picks(store, view.day, result)
    alerts.write_sent(store, view.day, {**sent, "evening": {
        "sent_at": now().isoformat(timespec="seconds"), "messages": len(messages), "research": result["status"],
        "candidates": result["candidates"], "shortlist": result["shortlist"],
        "picks": [p["candidate"]["symbol"] for p in picks], "analyses": started}})
    return {"status": "sent", "research": result["status"], "candidates": result["candidates"],
            "shortlist": len(result["shortlist"]), "picks": len(picks), "dropped": len(result["dropped"]),
            "analyses": len(started), "charts": len(photos)}


# ------------------------------------------------------------------ memory and learning
def research_dir(store: Store) -> Path:
    return store.root / "research"


def read_lessons(store: Store) -> str:
    try:
        return (research_dir(store) / "lessons.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_picks(store: Store) -> list[dict]:
    try:
        rows = json.loads((research_dir(store) / "picks.json").read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def record_picks(store: Store, day: date, result: dict[str, Any]) -> None:
    rows = [r for r in read_picks(store) if r.get("day") != day.isoformat()]
    for p in result["picks"]:
        c = p["candidate"]
        rows.append({"day": day.isoformat(), "symbol": c["symbol"], "pattern": c["pattern"], "kind": c["kind"],
                     "key": detection_key(c["record"]) if c.get("record") else None,
                     "start": str((c.get("record") or {}).get("start", ""))[:10],
                     "conviction": p["conviction"], "scores": p["scores"], "why_he": p["why_he"]})
    _write_json(research_dir(store) / "picks.json", rows)


def outcomes_of(picks: list[dict], ledger: pd.DataFrame | None) -> list[dict]:
    """Each pick with its outcome from the ledger (matched by symbol, pattern and start):
    target / failed / expired / open, or "no breakout yet" for a verge pick."""
    out = []
    for p in picks:
        outcome = "no breakout yet" if p.get("kind") == "verge" else "not in the ledger"
        if ledger is not None and not ledger.empty:
            rows = ledger.loc[(ledger["symbol"] == p["symbol"]) & (ledger["pattern"] == p["pattern"])
                              & (ledger["start_day"].astype(str).str[:10] == p.get("start", ""))]
            if len(rows):
                outcome = str(rows.iloc[-1]["outcome"])
        out.append({**p, "outcome": outcome})
    return out


def weekly_due(store: Store, now: datetime) -> bool:
    try:
        last = json.loads((research_dir(store) / "weekly.json").read_text(encoding="utf-8"))["at"]
        return now - datetime.fromisoformat(last) >= timedelta(days=6, hours=12)
    except (OSError, ValueError, KeyError):
        return True


def weekly_review(store: Store, llm, now: datetime, *, weeks: int = 12) -> dict[str, Any]:
    """The picks of the last `weeks` weeks against their outcomes and the baseline; the
    coach's new lessons (saved) and the owner's summary."""
    since = (now - timedelta(weeks=weeks)).date().isoformat()
    picks = outcomes_of([p for p in read_picks(store) if p.get("day", "") >= since], store.read_ledger())
    ledger = store.read_ledger()
    base = {}
    if ledger is not None and not ledger.empty:
        recent = ledger.loc[(ledger["breakout_day"].astype(str).str[:10] >= since)
                            & (ledger["direction"] == "bullish") & ledger["outcome"].isin(FINAL)]
        if len(recent):
            base = {"decided": len(recent), "target_pct": round((recent["outcome"] == "target").mean() * 100, 1),
                    "failed_pct": round((recent["outcome"] == "failed").mean() * 100, 1)}
    decided = [p for p in picks if p["outcome"] in FINAL]
    counts = {"picks": len(picks), "decided": len(decided),
              "target": sum(p["outcome"] == "target" for p in decided),
              "failed": sum(p["outcome"] == "failed" for p in decided)}
    user = _dumps({"picks": picks, "baseline_all_bullish_breakouts": base, "counts": counts,
                   "current_lessons": read_lessons(store) or "none yet"})
    schema = {"type": "object", "properties": {"lessons": {"type": "array", "items": {"type": "string"}},
                                               "summary_he": {"type": "string"}},
              "required": ["lessons", "summary_he"], "additionalProperties": False}
    answer, _ = llm.complete(system=agent_prompt("learning-coach"), user=user, schema=schema)
    lessons = [re.sub(r"\s+", " ", str(x)).strip() for x in answer.get("lessons") or [] if str(x).strip()][:10]
    summary = str(answer.get("summary_he", "")).strip()
    if text_problem(summary, _numbers(user)):
        summary = ""
    folder = research_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    if lessons:
        (folder / "lessons.md").write_text("\n".join(f"- {x}" for x in lessons) + "\n", encoding="utf-8")
    _write_json(folder / "weekly.json", {"at": now.isoformat(timespec="seconds"), "counts": counts,
                                         "baseline": base, "lessons": len(lessons)})
    return {"counts": counts, "baseline": base, "summary_he": summary, "lessons": len(lessons)}


def weekly_message(review: dict[str, Any]) -> str:
    c, b = review["counts"], review["baseline"]
    lines = ["📚 <b>סיכום שבועי של צוות המחקר</b>",
             f"בחירות ב-12 השבועות האחרונים: {c['picks']} · הוכרעו {c['decided']}: "
             f"{c['target']} הגיעו ליעד, {c['failed']} נכשלו."]
    if c["decided"]:
        lines.append(f"שיעור הצלחה של הבחירות: {c['target'] / c['decided'] * 100:.0f}%"
                     + (f" · לעומת {b['target_pct']:g}% בכל הפריצות השוריות" if b.get("target_pct") is not None else ""))
    if review.get("summary_he"):
        lines.append(html.escape(review["summary_he"]))
    lines.append(f"הצוות מחזיק עכשיו {review['lessons']} לקחים.")
    return "\n".join(lines)
