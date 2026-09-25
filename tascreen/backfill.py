"""Breakouts from past bars, found the way the daily scans would have found them.

For each stored stock the chart detector runs on the bars cut at every
`outcomes.backfill_step`-th session, from the first session with
`backfill_min_bars` of history up to the day before the first saved scan (from
then on the daily scans record them). Each cut sees nothing after it: the same
coverage gate as the scan (`scan.patterns_allowed`), the same detector, and each
breakout is kept the first time a cut reports it, as a daily scan would have taken
it in that day (`outcomes.rows_from_scan`, source "backfill"). With step 1 this is
the daily path exactly; tests/test_backfill.py checks it against run_scan plus
outcomes.update day by day.

Caveat shown on the site: the stocks are today's universe, so companies that fell
below $1B or were delisted are missing (survivorship bias).

Each symbol's rows are saved in data/outcomes/backfill/, so a stopped run resumes
where it stopped; a changed rule set, step or start starts over (manifest.json).
The ledger takes them in at the end (`outcomes.merge_backfill`), never changing a
row the daily scans recorded.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .config import OutcomesSettings
from .contracts import OUTCOMES
from .outcomes import TRACKED_STATUSES, empty_ledger, merge_backfill, rows_from_scan, update
from .patterns.chart import detect_chart
from .patterns.rules import Rules, load_rules
from .scan import patterns_allowed, patterns_frame
from .store import Store, symbol_file_stem

log = logging.getLogger(__name__)


def backfill_symbol(bars: pd.DataFrame, symbol: str, rules: Rules, *, step: int = 1,
                    min_bars: int = 150, before: date | None = None) -> list[dict[str, Any]]:
    """Ledger rows for the breakouts a daily scan would have taken in, cut by cut."""
    days = bars["timestamp"].dt.date
    last = len(bars) - 1
    if before is not None:
        earlier = (days < before).to_numpy().nonzero()[0]
        if not len(earlier):
            return []
        last = int(earlier[-1])
    cuts = list(range(min_bars - 1, last + 1, step))
    if cuts and cuts[-1] != last:
        cuts.append(last)                     # the last session is always looked at
    seen: dict[str, dict[str, Any]] = {}
    for cut in cuts:
        visible = bars.iloc[:cut + 1]
        if not patterns_allowed(visible, rules)[1]:
            continue
        found = [d for d in detect_chart(visible, symbol, rules)
                 if d.status in TRACKED_STATUSES and d.breakout_date is not None]
        if not found:
            continue
        for row in rows_from_scan(patterns_frame(found), days.iloc[cut], rules.digest,
                                  lambda _s: visible, source="backfill"):
            seen.setdefault(row["key"], row)
    return list(seen.values())


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return empty_ledger()
    return pd.DataFrame(rows, columns=list(OUTCOMES.columns))


_RULES: Rules | None = None


def _one(job: tuple[str, str, str, int, int, str | None]) -> tuple[str, int, float]:
    """One symbol, in a worker process: find, save, report (symbol, rows, seconds)."""
    global _RULES
    root, symbol, digest, step, min_bars, before = job
    started = time.monotonic()
    if _RULES is None:
        _RULES = load_rules()
    if _RULES.digest != digest:
        raise RuntimeError("rules.yaml changed while the backfill was running")
    store = Store(Path(root))
    bars = store.read_bars(symbol)
    rows = [] if bars is None else backfill_symbol(
        bars, symbol, _RULES, step=step, min_bars=min_bars,
        before=date.fromisoformat(before) if before else None)
    store.write_backfill_rows(symbol, _frame(rows))
    return symbol, len(rows), time.monotonic() - started


def run_backfill(store: Store, rules: Rules, cfg: OutcomesSettings, symbols: list[str], *,
                 max_sessions: int, report_path: Path | None = None,
                 progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Backfill every symbol not done yet, then merge the rows into the ledger. The cuts
    stop before the first saved scan made with these rules (scans made with other rules
    do not count: their days are backfilled too). A ledger holding rows found by other
    rules is rebuilt instead, so each breakout is counted under one definition."""
    scans = [d for d in store.scan_days() if store.scan_rules_digest(d) == rules.digest]
    before = scans[0].isoformat() if scans else None
    manifest = {"rules_digest": rules.digest, "step": cfg.backfill_step,
                "min_bars": cfg.backfill_min_bars, "before": before}
    if store.read_backfill_manifest() != manifest:
        progress("Backfill: rules, step or start changed (or first run): starting over")
        store.reset_backfill(manifest)
    done = store.backfill_done()
    todo = [s for s in symbols if symbol_file_stem(s) not in done]
    progress(f"Backfill: {len(todo)} to do, {len(symbols) - len(todo)} already done "
             f"(step {cfg.backfill_step}, cuts before {before or 'the last bar'}, "
             f"{cfg.backfill_workers} process(es))")
    jobs = [(str(store.root), s, rules.digest, cfg.backfill_step, cfg.backfill_min_bars, before)
            for s in todo]
    started = time.monotonic()
    found, failed, seconds = 0, {}, []

    def record(n: int, symbol: str, rows: int, took: float) -> None:
        nonlocal found
        found += rows
        seconds.append(took)
        if n % 25 == 0 or n == len(jobs):
            elapsed = time.monotonic() - started
            left = elapsed / n * (len(jobs) - n)
            progress(f"  {n}/{len(jobs)} symbols, {found} breakouts, "
                     f"{elapsed / 60:.1f} min so far, ~{left / 60:.0f} min left")

    if cfg.backfill_workers == 1:
        for n, job in enumerate(jobs, 1):
            try:
                record(n, *_one(job))
            except Exception as exc:  # noqa: BLE001 — one symbol must not stop the run
                log.exception("%s: backfill failed", job[1])
                failed[job[1]] = f"{type(exc).__name__}: {exc}"[:300]
    else:
        with ProcessPoolExecutor(max_workers=cfg.backfill_workers) as pool:
            futures = {pool.submit(_one, job): job[1] for job in jobs}
            try:
                for n, future in enumerate(as_completed(futures), 1):
                    try:
                        record(n, *future.result())
                    except Exception as exc:  # noqa: BLE001
                        log.error("%s: backfill failed: %s", futures[future], exc)
                        failed[futures[future]] = f"{type(exc).__name__}: {exc}"[:300]
            except KeyboardInterrupt:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
    ledger = store.read_ledger()
    rebuilt = ledger is not None and bool((ledger["rules_digest"] != rules.digest).any())
    if rebuilt:
        merged = update(store, max_sessions, rebuild=True, rules_digest=rules.digest)
    else:
        merged = merge_backfill(store, max_sessions)
    report = {"finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              **manifest, "symbols": len(jobs), "breakouts_found": found, "failed": failed,
              "seconds": round(time.monotonic() - started, 1),
              "seconds_per_symbol": round(sum(seconds) / len(seconds), 2) if seconds else None,
              "added_to_ledger": merged.get("backfill", 0), "ledger_rows": merged["rows"],
              "ledger_rebuilt": rebuilt,
              "outcomes": merged["outcomes"]}
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
