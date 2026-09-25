"""What happened after each chart-pattern breakout: the outcome ledger and the scorecard.

The ledger (data/outcomes/ledger.parquet, contract OUTCOMES) has one row per
breakout the daily scans reported (source "live"). The definitions are ours and
are stated on the site; they are not Bulkowski's statistics:
- tracking starts the session after the breakout day, from the breakout level;
- target: the first session whose high (bullish) or low (bearish) reaches the
  measure-rule target;
- failed: the first session that closes beyond the invalidation level
  (patterns/levels.py). A session that does both counts as failed: the order of
  the two inside one day is unknown;
- expired: neither within `max_sessions` sessions; open: still inside that window;
- no_data: the stored bars do not reach the breakout day.
mfe_pct is the best move in the breakout's direction and mae_pct the worst move
against it (0 when price never went back through the breakout level), both from
the breakout level, over the tracked sessions.

The first values seen for a pattern are kept (they are what the site showed); a
later scan that reports it differently (e.g. a flag refitted a day later) only
increments `restated`. A split rescales the stored bars: `scale` (the close on the
breakout day in today's bars over the close stored when the row was first seen)
carries the stored levels into today's units.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timezone
from typing import Any, Callable

import numpy as np
import pandas as pd

from .contracts import OUTCOMES
from .patterns.levels import day_of, detection_key, invalidation
from .store import Store

log = logging.getLogger(__name__)

OUTCOME_VALUES = ("open", "target", "failed", "expired", "no_data")
FINAL = frozenset({"target", "failed", "expired"})
TRACKED_STATUSES = ("breakout", "busted")
_NUMERIC = ("breakout_price", "breakout_close", "target", "invalidation", "height", "restated",
            "sessions", "mfe_pct", "mae_pct", "scale")


def empty_ledger() -> pd.DataFrame:
    frame = pd.DataFrame({c: pd.Series(dtype="float64" if c in _NUMERIC else "object")
                          for c in OUTCOMES.columns})
    return frame


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _close_on(bars: pd.DataFrame | None, day: date) -> float:
    if bars is None:
        return math.nan
    hit = bars.loc[bars["timestamp"].dt.date == day, "close"]
    return float(hit.iloc[0]) if len(hit) else math.nan


def rows_from_scan(patterns: pd.DataFrame, day: date, digest: str,
                   bars_of: Callable[[str], pd.DataFrame | None], source: str = "live") -> list[dict]:
    """Ledger rows for the chart breakouts in one scan's patterns."""
    if patterns.empty:
        return []
    picked = patterns[(patterns["family"] == "chart") & patterns["status"].isin(TRACKED_STATUSES)
                      & patterns["breakout_date"].notna()]
    rows = []
    for record in picked.to_dict("records"):
        bars = bars_of(record["symbol"])
        breakout_day = day_of(record["breakout_date"])
        rows.append({
            "key": detection_key(record), "symbol": record["symbol"], "pattern": record["pattern"],
            "direction": record["direction"], "start_day": day_of(record["start"]).isoformat(),
            "end_day": day_of(record["end"]).isoformat(), "breakout_day": breakout_day.isoformat(),
            "breakout_price": float(record["breakout_price"]),
            "breakout_close": _close_on(bars, breakout_day),
            "target": float(record["target"]), "invalidation": invalidation(record, bars),
            "height": float(record["height"]), "points_json": record["points_json"],
            "lines_json": record["lines_json"], "rules_digest": digest, "source": source,
            "first_seen": day.isoformat(), "restated": 0.0,
            "last_breakout_day": breakout_day.isoformat(), "outcome": "open", "resolved_day": None,
            "sessions": 0.0, "mfe_pct": math.nan, "mae_pct": math.nan, "scale": 1.0,
            "evaluated_through": None,
        })
    return rows


def merge(ledger: pd.DataFrame, rows: list[dict]) -> tuple[pd.DataFrame, dict[str, int]]:
    """Add new breakouts; a known key keeps its first values (see the module notes)."""
    counts = {"new": 0, "restated": 0}
    position = {key: i for i, key in enumerate(ledger["key"])}
    fresh: dict[str, dict] = {}
    ledger = ledger.copy()
    for row in rows:
        key = row["key"]
        if key in fresh:
            continue                        # the same pattern twice in one scan: keep the first
        if key not in position:
            fresh[key] = row
            continue
        i = position[key]
        known_day = ledger.at[i, "last_breakout_day"]
        moved = row["breakout_day"] != known_day
        price = float(ledger.at[i, "breakout_price"])
        stored_close = float(ledger.at[i, "breakout_close"])
        split = row["breakout_close"] / stored_close \
            if _finite(stored_close) and _finite(row["breakout_close"]) and stored_close > 0 else 1.0
        repriced = _finite(price) and _finite(row["breakout_price"]) and price > 0 and \
            abs(row["breakout_price"] / (price * split) - 1) > 0.005
        if moved or repriced:
            ledger.at[i, "restated"] = float(ledger.at[i, "restated"]) + 1
            ledger.at[i, "last_breakout_day"] = row["breakout_day"]
            counts["restated"] += 1
    if fresh:
        counts["new"] = len(fresh)
        added = pd.DataFrame(list(fresh.values()), columns=list(OUTCOMES.columns))
        ledger = added if ledger.empty else pd.concat([ledger, added], ignore_index=True)
    return ledger, counts


def add_new(ledger: pd.DataFrame, rows: list[dict]) -> tuple[pd.DataFrame, int]:
    """Append only the rows whose key the ledger does not have (backfilled rows never
    change a row the daily scans recorded)."""
    known = set(ledger["key"])
    fresh: dict[str, dict] = {}
    for row in rows:
        if row["key"] not in known and row["key"] not in fresh:
            fresh[row["key"]] = row
    if not fresh:
        return ledger, 0
    added = pd.DataFrame(list(fresh.values()), columns=list(OUTCOMES.columns))
    return (added if ledger.empty else pd.concat([ledger, added], ignore_index=True)), len(fresh)


def evaluate_row(row: dict[str, Any], bars: pd.DataFrame | None, max_sessions: int) -> dict[str, Any]:
    """The outcome fields for one ledger row, from today's stored bars."""
    none = {"outcome": "no_data", "resolved_day": None, "sessions": 0.0, "mfe_pct": math.nan,
            "mae_pct": math.nan, "scale": 1.0, "evaluated_through": None}
    if bars is None or bars.empty:
        return none
    days = bars["timestamp"].dt.date.to_numpy()
    where = np.flatnonzero(days == date.fromisoformat(row["breakout_day"]))
    if not len(where):
        return none
    i = int(where[0])
    scale = 1.0
    if _finite(row.get("breakout_close")) and float(row["breakout_close"]) > 0:
        scale = float(bars["close"].iloc[i]) / float(row["breakout_close"])
        if abs(scale - 1) < 1e-3:
            scale = 1.0
    level = float(row["breakout_price"]) * scale
    target = float(row["target"]) * scale if _finite(row.get("target")) else math.nan
    cancel = float(row["invalidation"]) * scale if _finite(row.get("invalidation")) else math.nan
    bullish = row["direction"] == "bullish"
    after = bars.iloc[i + 1:i + 1 + max_sessions]
    outcome, resolved, used = "open", None, len(after)
    highs, lows, closes = (after[c].to_numpy(float) for c in ("high", "low", "close"))
    for k in range(len(after)):
        failed = _finite(cancel) and (closes[k] < cancel if bullish else closes[k] > cancel)
        reached = _finite(target) and (highs[k] >= target if bullish else lows[k] <= target)
        if failed or reached:
            outcome, resolved, used = ("failed" if failed else "target"), days[i + 1 + k], k + 1
            break
    else:
        if len(after) >= max_sessions:
            outcome, resolved, used = "expired", days[i + max_sessions], max_sessions
    mfe = mae = math.nan
    if used and _finite(level) and level > 0:
        top, bottom = float(highs[:used].max()), float(lows[:used].min())
        if bullish:
            mfe, mae = (top / level - 1) * 100, max(0.0, (1 - bottom / level) * 100)
        else:
            mfe, mae = (1 - bottom / level) * 100, max(0.0, (top / level - 1) * 100)
    through = days[i + len(after)] if len(after) else days[i]
    return {"outcome": outcome, "resolved_day": resolved.isoformat() if resolved else None,
            "sessions": float(used), "mfe_pct": round(mfe, 2) if _finite(mfe) else math.nan,
            "mae_pct": round(mae, 2) if _finite(mae) else math.nan, "scale": round(scale, 6),
            "evaluated_through": through.isoformat()}


def evaluate(ledger: pd.DataFrame, bars_of: Callable[[str], pd.DataFrame | None],
             max_sessions: int, *, everything: bool = False) -> pd.DataFrame:
    """Re-evaluate every row not decided yet (all rows when `everything`)."""
    ledger = ledger.copy()
    for i, row in enumerate(ledger.to_dict("records")):
        if not everything and row["outcome"] in FINAL:
            continue
        for column, value in evaluate_row(row, bars_of(row["symbol"]), max_sessions).items():
            ledger.at[i, column] = value
    return ledger


def update(store: Store, max_sessions: int, *, rebuild: bool = False,
           now: datetime | None = None, rules_digest: str | None = None) -> dict[str, Any]:
    """Take in the scans not read yet, then evaluate. Rebuild starts from the saved scans,
    then adds the saved backfill (the scans' rows win). A rebuild given `rules_digest`
    keeps only what that rule set found: scans and backfill rows made with other rules
    are left out (scans are marked read, so later updates do not take them in either).
    A pattern renamed by a rule change would otherwise be counted under both names."""
    with store.ledger_lock():
        return _update(store, max_sessions, rebuild=rebuild, now=now, rules_digest=rules_digest)


def merge_backfill(store: Store, max_sessions: int, now: datetime | None = None) -> dict[str, Any]:
    """Add the saved backfill rows the ledger does not have yet, and evaluate them."""
    with store.ledger_lock():
        return _update(store, max_sessions, rebuild=False, now=now, backfill=True)


def _update(store: Store, max_sessions: int, *, rebuild: bool, now: datetime | None,
            backfill: bool = False, rules_digest: str | None = None) -> dict[str, Any]:
    meta = {} if rebuild else store.read_outcomes_meta()
    ledger = None if rebuild else store.read_ledger()
    if ledger is None:
        ledger, meta = empty_ledger(), {}
    # day -> the scan's created_at when it was read: a scan run again for the same day
    # (e.g. after a bar fetch cut short) is read again; merge() keeps first values.
    seen = meta.get("ingested", {})
    if isinstance(seen, list):                            # meta written before 2026-09-24
        seen = {day: None for day in seen}
    cache: dict[str, pd.DataFrame | None] = {}

    def bars_of(symbol: str) -> pd.DataFrame | None:
        if symbol not in cache:
            cache[symbol] = store.read_bars(symbol)
        return cache[symbol]

    counts = {"new": 0, "restated": 0}
    days = [d for d in store.scan_days()
            if d.isoformat() not in seen or seen[d.isoformat()] != _scan_created(store, d)]
    other_rules = []
    if rebuild and rules_digest:
        other_rules = [d for d in days if store.scan_rules_digest(d) != rules_digest]
        for day in other_rules:
            seen[day.isoformat()] = _scan_created(store, day)
        days = [d for d in days if d not in other_rules]
    for day in days:
        _, patterns, summary = store.read_scan(day)
        ledger, got = merge(ledger, rows_from_scan(patterns, day, summary.get("rules_digest", ""),
                                                   bars_of))
        counts = {k: counts[k] + got[k] for k in counts}
        seen[day.isoformat()] = summary.get("created_at")
    if rebuild or backfill:
        saved = store.read_backfill_rows()
        if saved is not None and rebuild and rules_digest:
            saved = saved[saved["rules_digest"] == rules_digest]
        if saved is not None:
            ledger, counts["backfill"] = add_new(ledger, saved.to_dict("records"))
    # a changed tracking window changes what "expired" means: evaluate every row again
    ledger = evaluate(ledger, bars_of, max_sessions,
                      everything=meta.get("max_sessions") not in (None, max_sessions))
    now = now or datetime.now(timezone.utc)
    store.write_ledger(ledger, {"ingested": dict(sorted(seen.items())), "max_sessions": max_sessions,
                                "updated_at": now.isoformat(timespec="seconds")})
    return {"scan_days_read": [d.isoformat() for d in days], "rows": len(ledger), **counts,
            "scans_other_rules": [d.isoformat() for d in other_rules],
            "outcomes": {k: int(v) for k, v in ledger["outcome"].value_counts().items()}}


def _scan_created(store: Store, day: date) -> str | None:
    path = store.scans_dir / day.isoformat() / "scan.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("created_at")
    except (OSError, ValueError):
        return None


def recent_decided(ledger: pd.DataFrame | None, count: int) -> list[dict[str, Any]]:
    """The newest decided breakouts, newest first."""
    if ledger is None or ledger.empty:
        return []
    done = ledger[ledger["outcome"].isin(FINAL)]
    done = done.sort_values(["resolved_day", "symbol"], ascending=[False, True]).head(count)
    return done.to_dict("records")


def scorecard(ledger: pd.DataFrame | None, min_cases: int) -> list[dict[str, Any]]:
    """One row per (pattern, source): counts, and percentages of the decided breakouts
    only when there are at least `min_cases` of them."""
    if ledger is None or ledger.empty:
        return []
    out = []
    for (pattern, source), group in ledger.groupby(["pattern", "source"], sort=False):
        counts = group["outcome"].value_counts()
        decided = int(sum(counts.get(k, 0) for k in FINAL))
        enough = decided >= min_cases

        def share(kind: str) -> float | None:
            return round(counts.get(kind, 0) / decided * 100, 1) if enough else None

        reached = group[group["outcome"] == "target"]
        done = group[group["outcome"].isin(FINAL)]
        out.append({
            "pattern": pattern, "source": source, "breakouts": len(group),
            "open": int(counts.get("open", 0)), "no_data": int(counts.get("no_data", 0)),
            "decided": decided, "target": int(counts.get("target", 0)),
            "failed": int(counts.get("failed", 0)), "expired": int(counts.get("expired", 0)),
            "target_pct": share("target"), "failed_pct": share("failed"),
            "expired_pct": share("expired"), "enough": enough,
            "has_target": bool(group["target"].notna().any()),
            "median_sessions_to_target": float(reached["sessions"].median()) if len(reached) else None,
            "median_best_move_pct": float(done["mfe_pct"].median()) if done["mfe_pct"].notna().any() else None,
        })
    out.sort(key=lambda r: (r["source"] != "live", -r["breakouts"], r["pattern"]))
    return out
