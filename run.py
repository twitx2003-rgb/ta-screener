#!/usr/bin/env python
"""Command line for ta-screener.

    .venv\\Scripts\\python.exe run.py --auth-tradingview     # once, in the browser
    .venv\\Scripts\\python.exe run.py --discover             # look at the live tools

Exit codes: 0 ok, 1 failed, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from tascreen.config import load_settings
from tascreen.errors import ScreenerError
from tascreen.logging_setup import setup_logging

log = logging.getLogger("run")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py",
                                     description="Technical-analysis stock screener (personal use)")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--auth-tradingview", action="store_true",
                        help="Sign in to TradingView's MCP server in the browser (once), then exit")
    parser.add_argument("--tradingview-token-status", action="store_true",
                        help="Show whether a TradingView sign-in is stored, when it expires and "
                             "whether it can be renewed (prints no secrets)")
    parser.add_argument("--tradingview-diagnose", action="store_true",
                        help="Show which OAuth sign-in routes TradingView's server offers")
    parser.add_argument("--tradingview-probe", metavar="URL",
                        help="Follow a TradingView sign-in URL hop by hop and report where it "
                             "is blocked")
    parser.add_argument("--tradingview-tools", action="store_true",
                        help="List the tools TradingView's MCP server offers "
                             "(full schemas saved to logs/tradingview_tools.json)")
    parser.add_argument("--tradingview-call", nargs="+", metavar=("TOOL", "KEY=VALUE"),
                        help="Call one read-only TradingView tool and print the raw result, e.g. "
                             "--tradingview-call mcp-tv-get-ohlcv symbol=NASDAQ:AAPL count=5")
    parser.add_argument("--discover", nargs="*", metavar="PROBE", default=None,
                        help="Probe the screener, its column catalogue and get-ohlcv; save the "
                             "payloads to logs/discover/ so their shapes can be mapped. "
                             "Name probes to run only those (e.g. --discover screener_top5)")
    parser.add_argument("--universe", action="store_true",
                        help="Fetch every US stock above universe.min_market_cap from "
                             "TradingView's screener -> data/universe/<date>.parquet")
    parser.add_argument("--bars", action="store_true",
                        help="Fetch or update daily bars for the newest universe -> data/bars/")
    parser.add_argument("--update", action="store_true",
                        help="--universe, --bars, then --scan (a rate-limited screener falls back "
                             "to the newest saved universe up to universe.max_age_days old)")
    parser.add_argument("--check-bars", action="store_true",
                        help="Check stored bars against the trading calendar: market-wide "
                             "missing days, per-symbol gaps, symbols behind the last session")
    parser.add_argument("--scan", action="store_true",
                        help="Indicators, candlestick and chart patterns for every symbol with "
                             "bars -> data/scans/<last session>/ (no TradingView calls)")
    parser.add_argument("--scan-symbol", metavar="SYMBOL",
                        help="Scan one symbol from stored bars and print every detection with "
                             "its rule checklist (e.g. NASDAQ:NVDA)")
    parser.add_argument("--outcomes", action="store_true",
                        help="Update the breakout ledger from the saved scans: what happened after "
                             "each chart-pattern breakout (target, failed, open) -> data/outcomes/")
    parser.add_argument("--backfill-outcomes", action="store_true",
                        help="Find past breakouts: the chart detector on stored bars, one past "
                             "session at a time without looking ahead (hours; resumable; "
                             "--limit N for the N largest stocks) -> the outcome ledger")
    parser.add_argument("--rebuild", action="store_true",
                        help="With --outcomes: build the ledger again from every saved scan")
    parser.add_argument("--ci-probe", action="store_true",
                        help="Stage-0 check on a GitHub runner: TradingView (forced token refresh, "
                             "a screener pass, --limit N get-ohlcv calls, default 300) and one "
                             "Claude call; prints counts and timings only (public logs)")
    parser.add_argument("--ci-tick", action="store_true",
                        help="GitHub Actions: do whatever is due now (the daily update and the "
                             "channels after a session closes); logs/ci_summary.json gets counts "
                             "only, for the public log")
    parser.add_argument("--no-channels", action="store_true",
                        help="With --ci-tick: skip the channels (a test run)")
    parser.add_argument("--max-minutes", type=float, metavar="M",
                        help="With --update/--bars/--ci-tick: stop fetching bars after M minutes "
                             "(the rest are deferred to the next run); with --ci-live: start the "
                             "continuation after M minutes (default 345)")
    parser.add_argument("--export-site", metavar="DIR",
                        help="Write the website as static files to DIR (for Vercel): every page, "
                             "no live data")
    parser.add_argument("--setup-telegram", action="store_true",
                        help="Connect your private Telegram bot (made with @BotFather): asks for "
                             "its token (hidden), finds your chat, sends a test message")
    parser.add_argument("--ci-notify", metavar="STATUS",
                        help="GitHub Actions: send the owner a Telegram summary of this run "
                             "(STATUS = the job's status)")
    parser.add_argument("--analyze", metavar="SYMBOL",
                        help="Technical analysis of one stock (NVDA or NASDAQ:NVDA) from its stored "
                             "bars: the facts, a chart, a Pine Script and a Hebrew text written by "
                             "Claude Code (normal terminal only) -> --out (default logs/analyses/)")
    parser.add_argument("--no-llm", action="store_true",
                        help="With --analyze: facts, chart and Pine Script only, no written analysis")
    parser.add_argument("--telegram", action="store_true",
                        help="With --analyze: also send the chart, the text and the Pine Script "
                             "to your Telegram bot")
    parser.add_argument("--out", metavar="DIR", help="With --analyze: where to write the files")
    parser.add_argument("--telegram-webhook", metavar="URL",
                        help="Point the Telegram bot at the site's webhook (https://.../api/telegram/), "
                             "or 'off' to remove it")
    parser.add_argument("--ci-live", action="store_true",
                        help="GitHub Actions (live.yml): during the session, alert on prices "
                             "crossing a bullish pattern's breakout line (--max-minutes, then "
                             "the job starts its continuation)")
    parser.add_argument("--ci-analyze", metavar="SYMBOL",
                        help="GitHub Actions (analyst.yml): one requested analysis sent to Telegram, "
                             "kept under --archive, within --daily-limit")
    parser.add_argument("--archive", metavar="DIR", default="analyses",
                        help="With --ci-analyze: the analyses kept so far (one folder per UTC day)")
    parser.add_argument("--daily-limit", type=int, default=10,
                        help="With --ci-analyze: analyses per UTC day, at most")
    parser.add_argument("--quotes", action="store_true",
                        help="Fetch every stock's last price once from TradingView's screener "
                             "-> data/quotes/ (the website shows them and live pattern crossings)")
    parser.add_argument("--live", action="store_true",
                        help="Keep running: quotes every live.interval_minutes during the US "
                             "session, then the daily update (universe, bars, scan) after the close")
    parser.add_argument("--channels", action="store_true",
                        help="Write today's discussion channels: simulated members (AI agents) discuss "
                             "the most common patterns, through Claude Code (run from a normal terminal)")
    parser.add_argument("--redraw-charts", action="store_true",
                        help="Draw the channel chart images again with the current chart style "
                             "(no model calls)")
    parser.add_argument("--force", action="store_true",
                        help="With --channels: write channels again even if already written today")
    parser.add_argument("--serve", action="store_true",
                        help="Open the website on http://127.0.0.1:<web.port>/ (this computer only; "
                             "reads the newest scan, never calls TradingView)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="With --bars/--update/--backfill-outcomes: only the N largest "
                             "symbols (a pilot run)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def make_tradingview(settings, interactive: bool = False):
    from tascreen.tv.mcp_client import TradingViewMCP

    tv = settings.tradingview
    return TradingViewMCP(url=tv.url, token_path=tv.token_path, callback_host=tv.callback_host,
                          callback_port=tv.callback_port, interactive=interactive)


def auth_tradingview(settings) -> int:
    client = make_tradingview(settings, interactive=True)
    tools = client.list_tools()
    print(f"\nSigned in. TradingView's MCP server offers {len(tools)} tools.")
    print(f"Tokens stored in {client.storage.path} (outside the project; never commit it).")
    print("Next: .venv\\Scripts\\python.exe run.py --discover\n")
    return 0


def tradingview_token_status(settings) -> int:
    print()
    for key, value in make_tradingview(settings).storage.status().items():
        print(f"  {key:<14} {value}")
    print()
    return 0


def tradingview_diagnose(settings) -> int:
    from tascreen.tv.mcp_client import diagnose

    print("\n" + "\n".join(diagnose(settings.tradingview.url)) + "\n")
    return 0


def tradingview_probe(url: str) -> int:
    from tascreen.tv.mcp_client import probe_url

    print("\n" + "\n".join(probe_url(url)) + "\n")
    return 0


def tradingview_tools(settings) -> int:
    from tascreen.tv.mcp_client import describe_tools

    tools = make_tradingview(settings).list_tools()
    print(f"\n{len(tools)} tools:\n")
    print(describe_tools(tools))
    target = settings.log_dir / "tradingview_tools.json"
    target.write_text(json.dumps([t.model_dump(mode="json") for t in tools], indent=2,
                                 ensure_ascii=False), encoding="utf-8")
    print(f"\nFull schemas saved to {target}\n")
    return 0


def tradingview_call(settings, spec: list[str]) -> int:
    from tascreen.tv.data import RateLimited, tool_payload
    from tascreen.tv.mcp_client import describe_result, parse_tool_args

    name, args = spec[0], parse_tool_args(spec[1:])
    client = make_tradingview(settings)
    print(f"\ncalling {name}({args})\n")
    for delay in (*settings.tradingview.rate_limit_delays, None):
        result = client.call_tool(name, args)
        try:
            tool_payload(result, name)
        except RateLimited:
            if delay is not None:
                print(f"rate limited by TradingView (429) - retrying in {delay:.0f}s")
                time.sleep(delay)
                continue
        except Exception:  # noqa: BLE001 — show the raw result whatever the failure
            pass
        break
    print(describe_result(result))
    print()
    return 0


def discover(settings, names: list[str]) -> int:
    from tascreen.discover import PROBES, print_report, run_probes

    known = {p.name for p in PROBES}
    unknown = sorted(set(names) - known)
    if unknown:
        log.error("unknown probe(s) %s; known: %s", unknown, sorted(known))
        return 2
    probes = tuple(p for p in PROBES if not names or p.name in names)
    out_dir = settings.log_dir / "discover"
    report = run_probes(make_tradingview(settings), out_dir,
                        delays=settings.tradingview.rate_limit_delays, probes=probes)
    print_report(report, out_dir)
    return 0 if all(e["status"] == "ok" for e in report) else 1


def _market_today(settings):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(settings.market.timezone)).date()


def universe(settings) -> int:
    from tascreen.store import Store
    from tascreen.universe import fetch_universe

    client = make_tradingview(settings)
    frame, summary = client.with_session(lambda session: fetch_universe(
        session, settings.universe, delays=settings.tradingview.rate_limit_delays))
    path = Store(settings.data_dir).write_universe(_market_today(settings), frame, summary)
    print(f"\nUniverse: {summary['kept']} stocks above ${settings.universe.min_market_cap / 1e9:g}B "
          f"(screener total {summary['total_including_dropped']}, dropped {summary['dropped']})")
    print(f"  exchanges: {summary['exchanges']}")
    print(f"  subtypes:  {summary['subtypes']}")
    print(f"  bands:     {len(summary['bands'])} "
          f"({', '.join(str(b['rows']) for b in summary['bands'])} rows)")
    print(f"  saved:     {path}\n")
    return 0


def usable_universe(settings, *, max_age_days: float | None = None):
    """The newest saved universe, or None (with the reason logged) if absent or too old."""
    from tascreen.store import Store

    latest = Store(settings.data_dir).latest_universe()
    if latest is None:
        log.error("no saved universe; run: .venv\\Scripts\\python.exe run.py --universe")
        return None
    day, frame = latest
    age = (_market_today(settings) - day).days
    if max_age_days is not None and age > max_age_days:
        log.error("the newest saved universe is from %s (%d days old, limit %g)", day, age,
                  max_age_days)
        return None
    return day, frame


def bars(settings, limit: int | None, stop_at=None) -> int:
    from tascreen.bars import BarsJob, update_all
    from tascreen.store import Store

    found = usable_universe(settings)
    if found is None:
        return 1
    day, frame = found
    symbols = frame["symbol"].tolist()[:limit] if limit else frame["symbol"].tolist()
    job = BarsJob(Store(settings.data_dir), settings.bars, settings.market,
                  settings.tradingview.rate_limit_delays, stop_at=stop_at)
    print(f"\nBars for {len(symbols)} symbols (universe of {day}); "
          f"last completed session: {job.target}\n")
    report = update_all(make_tradingview(settings), job, symbols)
    (settings.log_dir / "bars_last_run.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDone in {report['seconds']:.0f} s, {report['calls']} calls "
          f"({report['seconds_per_call']} s/call): {report['counts']}")
    if report["deferred"]:
        print(f"  deferred to the next run (deadline {stop_at}): {report['deferred']}")
    for kind in ("failed", "stale", "refetched"):
        if report[kind]:
            print(f"  {kind} ({len(report[kind])}):")
            for symbol, note in list(report[kind].items())[:15]:
                print(f"    {symbol}: {note[:150]}")
    print(f"  full report: {settings.log_dir / 'bars_last_run.json'}\n")
    return 1 if report["failed"] else 0


def check_bars(settings) -> int:
    from datetime import datetime, timezone

    from tascreen.bars import audit_bars
    from tascreen.market_hours import last_completed_session
    from tascreen.store import Store

    found = usable_universe(settings)
    if found is None:
        return 1
    _, frame = found
    target = last_completed_session(datetime.now(timezone.utc), market_tz=settings.market.timezone,
                                    session_close=settings.market.session_close)
    report = audit_bars(Store(settings.data_dir), frame["symbol"].tolist(), target)
    (settings.log_dir / "bars_audit.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nBars audit ({report['symbols_checked']} symbols with bars, target session "
          f"{report['target_session']}, {report['rows_min']}-{report['rows_max']} bars each)")
    print(f"  no bars yet:              {len(report['absent'])}")
    print(f"  behind the last session:  {len(report['behind_target'])}")
    print(f"  market-wide missing days: {report['market_wide_missing_days'] or 'none'}"
          + ("  <- add to market_hours.UNSCHEDULED_CLOSURES if the exchange was closed"
             if report["market_wide_missing_days"] else ""))
    print(f"  symbols with other gaps:  {len(report['symbols_with_gaps'])}")
    for symbol, gaps in list(report["symbols_with_gaps"].items())[:10]:
        print(f"    {symbol}: {gaps[:5]}{' ...' if len(gaps) > 5 else ''}")
    print(f"  bars on non-trading days: {len(report['bars_on_non_trading_days'])}")
    print(f"  sparse series (<95% of sessions): {report['sparse_series'] or 'none'}")
    print(f"  full report: {settings.log_dir / 'bars_audit.json'}\n")
    bad = report["market_wide_missing_days"] or report["bars_on_non_trading_days"]
    return 1 if bad else 0


def _target(settings):
    from datetime import datetime, timezone

    from tascreen.market_hours import last_completed_session

    return last_completed_session(datetime.now(timezone.utc), market_tz=settings.market.timezone,
                                  session_close=settings.market.session_close)


def scan(settings) -> int:
    from tascreen.patterns.rules import load_rules
    from tascreen.scan import run_scan
    from tascreen.store import Store

    found = usable_universe(settings)
    if found is None:
        return 1
    day, frame = found
    target = _target(settings)
    print(f"\nScanning {len(frame)} symbols (universe of {day}) for session {target}\n")
    summary = run_scan(Store(settings.data_dir), frame, day, load_rules(), target)
    print(f"\nScan done in {summary['seconds']:.0f} s: {summary['symbols_scanned']} symbols, "
          f"{summary['detections']} detections")
    print(f"  without bars: {len(summary['symbols_without_bars'])}, "
          f"behind the session: {len(summary['symbols_behind_session'])}, "
          f"errors: {len(summary['errors'])}")
    for pattern, statuses in summary["counts"].items():
        print(f"    {pattern:<22} {statuses}")
    try:
        outcomes(settings, quiet=True)
    except (ScreenerError, OSError) as exc:      # the scan itself is fine; the ledger waits
        log.error("outcome ledger not updated: %s", exc)
    print("  our indicators vs TradingView's:")
    for name, result in summary["cross_check_vs_tradingview"].items():
        if result.get("compared"):
            print(f"    {name:<8} {result['agree']}/{result['compared']} agree within "
                  f"{result['tolerance']} (median diff {result['median_diff']})")
    print()
    return 1 if summary["errors"] else 0


def outcomes(settings, rebuild: bool = False, quiet: bool = False) -> int:
    from tascreen.outcomes import update as update_outcomes
    from tascreen.store import Store

    report = update_outcomes(Store(settings.data_dir), settings.outcomes.max_sessions,
                             rebuild=rebuild)
    line = (f"Outcomes: {report['rows']} breakouts tracked ({report['new']} new, "
            f"{report['restated']} restated) {report['outcomes']}")
    if quiet:
        log.info("%s", line)
    else:
        print(line)
    return 0


def backfill_outcomes(settings, limit: int | None) -> int:
    from tascreen.backfill import run_backfill
    from tascreen.patterns.rules import load_rules
    from tascreen.store import Store

    store = Store(settings.data_dir)
    found = store.latest_universe()
    if found is None:
        log.error("no universe yet; run --universe first")
        return 1
    _, frame = found
    symbols = [s for s in frame.sort_values("market_cap", ascending=False)["symbol"]
               if store.bars_path(s).exists()][:limit]
    report = run_backfill(store, load_rules(), settings.outcomes, symbols,
                          max_sessions=settings.outcomes.max_sessions,
                          report_path=settings.log_dir / "backfill_last_run.json")
    print(f"\nBackfill done in {report['seconds'] / 60:.1f} min "
          f"({report['seconds_per_symbol']} s per symbol in one process): "
          f"{report['breakouts_found']} breakouts found, {report['added_to_ledger']} added to the "
          f"ledger ({report['ledger_rows']} rows) {report['outcomes']}")
    if report["failed"]:
        print(f"  failed: {len(report['failed'])} symbol(s), see logs/backfill_last_run.json")
    return 1 if report["failed"] else 0


def ci_probe(settings, calls: int | None) -> int:
    from tascreen.ci import probe
    from tascreen.llm import ClaudeCodeLLM

    report = probe(settings, make_tradingview(settings),
                   lambda: ClaudeCodeLLM(model=settings.channels.model, effort="low", timeout_s=180),
                   calls=calls or 300)
    print(json.dumps(report, indent=2))
    return 0 if report["tradingview"].get("ok") and report["claude"].get("ok") else 1


def ci_tick(settings, limit: int | None, max_minutes: float | None, with_channels: bool) -> int:
    """What is due now on a GitHub runner. The daily update runs when the last completed
    session has no complete update yet (a run cut short, e.g. by a throttled TradingView,
    is finished by the next one); then the channels. The public log shows only
    logs/ci_summary.json: dates, counts and error class names."""
    from datetime import datetime, timedelta, timezone

    from tascreen.market_hours import next_open
    from tascreen.store import Store

    started = datetime.now(timezone.utc)
    store = Store(settings.data_dir)
    target = _target(settings)
    state = store.read_live_state()
    due = state.get("daily_update_for") != target.isoformat() or not state.get("complete", True)
    summary: dict = {"session": target.isoformat(), "due": due}
    code = 0
    if due:
        deadline = next_open(started, market_tz=settings.market.timezone) - timedelta(minutes=10)
        if max_minutes:
            deadline = min(deadline, started + timedelta(minutes=max_minutes))
        try:
            code = update(settings, limit, stop_at=deadline)
        except ScreenerError as exc:
            log.error("daily update failed: %s", exc)          # the private log only
            code, summary["update_error"] = 1, type(exc).__name__
        last = _read_log_json(settings, "bars_last_run.json")
        summary["bars"] = last.get("counts", {})
        deferred = int(summary["bars"].get("deferred", 0))
        scans = store.scan_days()
        if scans and scans[-1] == target:
            scan_summary = store.read_scan(target)[2]
            summary["scan"] = {k: scan_summary.get(k) for k in
                               ("symbols_in_universe", "symbols_scanned", "detections")}
        summary["outcomes"] = store.read_outcomes_meta().get("updated_at")
        complete = code == 0 and deferred == 0 and bool(scans) and scans[-1] == target
        if with_channels and settings.channels.enabled and scans and scans[-1] == target:
            try:
                channels(settings)
            except ScreenerError as exc:
                summary["channels_error"] = type(exc).__name__
            report = _read_log_json(settings, "channels_last_run.json").get("channels", {})
            summary["channels"] = {
                "written": sum(1 for v in report.values() if isinstance(v, dict)),
                "already": sum(1 for v in report.values() if v == "already written"),
                "failed": sum(1 for v in report.values() if str(v).startswith("failed")),
                "skipped": sum(1 for v in report.values() if str(v).startswith("skipped"))}
        store.write_live_state({"daily_update_for": target.isoformat(), "complete": complete,
                                "exit_code": code,
                                "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        summary["complete"] = complete
    if settings.alerts.enabled:
        summary["alerts"] = _evening_alerts(settings, store, target)
    summary["seconds"] = round((datetime.now(timezone.utc) - started).total_seconds())
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    (settings.log_dir / "ci_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if not due or summary.get("complete") else 1


def _evening_alerts(settings, store, target) -> dict:
    """The breakout report (tascreen/alerts.py), once per session: after a complete
    update, or anyway from the morning catch-up (8 hours after the close), so an update
    cut short still reports. Counts only: this goes to the public log."""
    import os
    from datetime import datetime, timedelta, timezone

    from tascreen import github, notify
    from tascreen.alerts import evening_report
    from tascreen.market_hours import session_bounds
    from tascreen.patterns.rules import load_rules
    from tascreen.web.data import ScanRepository

    scans = store.scan_days()
    if not scans or scans[-1] != target:
        return {"status": "no scan of the session yet"}
    state = store.read_live_state()
    complete = state.get("daily_update_for") == target.isoformat() and state.get("complete")
    close = session_bounds(target, settings.market.timezone)[1]
    if not complete and datetime.now(timezone.utc) < close + timedelta(hours=8):
        return {"status": "waiting for a complete update"}
    bot = notify.from_environment()
    if bot is None:
        return {"status": "telegram not configured"}
    try:
        return evening_report(store, ScanRepository(store, load_rules()).current(), settings.alerts,
                              bot=bot, min_cases=settings.outcomes.min_cases, dispatch=github.dispatch,
                              can_dispatch=bool(os.environ.get("GH_DISPATCH_TOKEN", "").strip()))
    except ScreenerError as exc:
        log.error("breakout report failed: %s", exc)             # the private log only
        return {"status": "failed", "error": type(exc).__name__}


def ci_live(settings, max_minutes: float) -> int:
    """GitHub Actions (live.yml): during the session, price the stocks whose bullish
    pattern is near its breakout line and tell the owner when a price crosses it (not
    final until the close). A runner job lasts 6 hours at most and the session is longer,
    so after `max_minutes` the job starts its own continuation. The public log shows
    logs/live_summary.json: counts and seconds only."""
    import statistics
    import time as clock
    from datetime import datetime, timedelta, timezone

    from tascreen import alerts, github, notify
    from tascreen.errors import ProviderError
    from tascreen.market_hours import live_session, next_open, session_bounds
    from tascreen.patterns.rules import load_rules
    from tascreen.store import Store
    from tascreen.tv.mcp_client import push_token_file
    from tascreen.web.data import ScanRepository

    cfg, tz = settings.alerts, settings.market.timezone
    started = datetime.now(timezone.utc)
    summary: dict = {"started": started.isoformat(timespec="seconds"), "passes": 0, "failed_passes": 0,
                     "calls": 0, "failed_calls": 0, "median_call_s": None, "alerts": 0}

    def finish(ended: str, code: int = 0) -> int:
        summary["ended"] = ended
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        (settings.log_dir / "live_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return code

    bot = notify.from_environment()
    if bot is None or not cfg.enabled:
        return finish("telegram not configured" if bot is None else "alerts are off", 1 if bot is None else 0)
    after = settings.live.after_close_minutes
    day = live_session(started, market_tz=tz, after_close_minutes=after)
    if day is None:
        opens = next_open(started, market_tz=tz)
        if opens - started > timedelta(minutes=15):
            return finish("the market is closed")        # the other start time covers winter/summer
        clock.sleep((opens - started).total_seconds())
        day = live_session(datetime.now(timezone.utc), market_tz=tz, after_close_minutes=after)
    store = Store(settings.data_dir)
    view = ScanRepository(store, load_rules()).current()
    if view is None:
        return finish("no scan")
    watch = alerts.watch_list(view, cfg.watch_pct, cfg.live_max_symbols)
    summary["watched"] = len(watch)
    if not watch:
        return finish("nothing near a breakout line")
    ends = session_bounds(day, tz)[1] + timedelta(minutes=after)
    hand_over = started + timedelta(minutes=max_minutes)
    interval = cfg.live_interval_minutes
    sent = alerts.read_sent(store, day)
    client = make_tradingview(settings)
    all_seconds: list[float] = []
    while True:
        now = datetime.now(timezone.utc)
        if now >= ends:
            return finish("the session is over")
        if now >= hand_over:
            status = github.dispatch("live.yml", {"continued": "yes"})
            return finish(f"handed over ({status})", 0 if status == 204 else 1)
        pass_started = clock.monotonic()
        try:
            got = client.with_session(lambda session: alerts.fetch_live_prices(
                session, watch, day, tz, concurrency=settings.bars.concurrency))
        except (ProviderError, OSError, TimeoutError, ExceptionGroup) as exc:
            log.warning("a price pass failed: %s", type(exc).__name__)
            summary["failed_passes"] += 1
            got = {"prices": {}, "seconds": [], "failed": 0}
        summary["passes"] += 1
        summary["calls"] += len(got["seconds"]) + got["failed"]
        summary["failed_calls"] += got["failed"]
        all_seconds += got["seconds"]
        if all_seconds:
            summary["median_call_s"] = round(statistics.median(all_seconds), 2)
        found = alerts.live_crossings(view, got["prices"], day, sent.get("live", {}))
        if found:
            bot.send(alerts.live_message(found, datetime.now(timezone.utc), tz, cfg.site_url), html=True)
            live = sent.setdefault("live", {})
            for c in found:
                live[c["key"]] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "price": c["price"], "line": c["line"]}
            alerts.write_sent(store, day, sent)
            push_token_file(alerts.sent_path(store, day), "live alerts sent")
            summary["alerts"] += len(found)
        # TradingView slows down after heavy use: a slow pass stretches the interval
        if got["seconds"] and statistics.median(got["seconds"]) > 5:
            interval = min(interval * 2, 60.0)
        wait = pass_started + interval * 60 - clock.monotonic()
        limit = min(ends, hand_over) - datetime.now(timezone.utc)
        clock.sleep(max(0.0, min(wait, limit.total_seconds())))


def _read_log_json(settings, name: str) -> dict:
    path = settings.log_dir / name
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def export_site(settings, out: str) -> int:
    from tascreen.web.export import export_site as export

    report = export(settings, Path(out))
    print(json.dumps(report))
    return 0


def setup_telegram(settings) -> int:
    import getpass

    from tascreen.notify import Telegram, save_credentials

    print("\nחיבור בוט הטלגרם. הדבק את המפתח (token) שקיבלת מ-BotFather ולחץ Enter.")
    print("המפתח לא יוצג על המסך בזמן ההדבקה, וזה תקין.\n")
    token = getpass.getpass("Token: ").strip()
    bot = Telegram(token)
    try:
        name = bot.bot_name()
    except ScreenerError as exc:
        print(f"\nהמפתח לא עובד: {exc}\n")
        return 1
    print(f"\nהבוט @{name} מחובר. אם עוד לא שלחת לו הודעה בטלגרם, שלח עכשיו (מחכה עד 2 דקות)...")
    hooked = bot.webhook_url()           # Telegram refuses getUpdates while a webhook is set
    if hooked:
        bot.delete_webhook()
    try:
        chat = bot.find_private_chat(wait_s=120)
    finally:
        if hooked:
            bot.set_webhook(hooked)
    if chat is None:
        print("\nלא הגיעה לבוט הודעה. שלח לו הודעה כלשהי בטלגרם והרץ את הפקודה שוב.\n")
        return 1
    bot.chat_id = chat
    bot.send("✅ הבוט של הסורק הטכני מחובר. מכאן יגיעו התראות על העדכונים.")
    path = save_credentials(token, chat)
    print(f"\nנשלחה אליך הודעת בדיקה בטלגרם. הפרטים נשמרו במחשב: {path}")
    print("\nעכשיו שמור ב-GitHub שני סודות (Settings > Secrets and variables > Actions):")
    print("  TELEGRAM_BOT_TOKEN  = אותו מפתח שהדבקת עכשיו")
    print(f"  TELEGRAM_CHAT_ID    = {chat}\n")
    return 0


def telegram_webhook(settings, url: str) -> int:
    """Point the bot at the site's webhook function (run.yml after each deployment), or
    'off' to remove it. Prints no URL parameters and no secret."""
    from tascreen.notify import from_environment

    bot = from_environment()
    if bot is None:
        print("telegram: not configured")
        return 1
    if url == "off":
        bot.delete_webhook()
        print("webhook: removed")
        return 0
    if not url.startswith("https://") or not url.endswith("/"):
        log.error("the webhook must be an https:// address ending in / (Telegram does not "
                  "follow the site's trailing-slash redirect)")
        return 2
    bot.set_webhook(url)
    print("webhook: set")
    return 0


def ci_notify(settings, status: str) -> int:
    import os

    from tascreen.notify import from_environment, run_message

    bot = from_environment()
    if bot is None:
        print("telegram: not configured")
        return 0
    run_url = ""
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                   f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    site_file = settings.log_dir / "site-url.txt"
    site = site_file.read_text(encoding="utf-8").strip() if site_file.exists() else ""
    text = run_message(_read_log_json(settings, "ci_summary.json"), status, run_url, site)
    if not text:
        print("telegram: nothing to report")
        return 0
    try:
        bot.send(text)
        print("telegram: sent")
    except ScreenerError as exc:
        print(f"telegram: {exc}")
    return 0


def _analyst_llm(settings):
    from tascreen.analyst import writer
    from tascreen.llm import ClaudeCodeLLM

    cfg = settings.channels
    return ClaudeCodeLLM(model=cfg.model, effort=writer.EFFORT, timeout_s=cfg.timeout_s)


def analyze(settings, text: str, out: str | None, with_llm: bool, to_telegram: bool) -> int:
    from tascreen import notify
    from tascreen.analyst import find_symbol
    from tascreen.analyst.request import produce
    from tascreen.store import Store

    store = Store(settings.data_dir)
    symbol = find_symbol(store.bars_dir, text)
    bot = notify.from_environment() if to_telegram else None
    if to_telegram and bot is None:
        log.error("telegram is not set up (run.py --setup-telegram)")
        return 1
    llm = _analyst_llm(settings) if with_llm else None
    if llm is not None:
        print(f"writing the analysis with {llm.name} (a minute or two)...")
    folder = Path(out) if out else settings.log_dir / "analyses"
    done = produce(symbol, store.read_bars(symbol), folder, llm=llm, bot=bot)
    analysis, written = done["analysis"], done["written"]
    print(f"\n{symbol}, {analysis.last_day}: {len(analysis.facts)} facts -> {folder / done['stem']}.*")
    if written is None:
        for key, fact in analysis.facts.items():
            print(f"  {key:<24} {fact['value']}{fact['unit']}   {fact['label']}")
    else:
        print(f"{len(written['parts'])} sections kept, {len(written['dropped'])} dropped"
              + (f", left out: {', '.join(written['omitted'])}" if written["omitted"] else "")
              + f" ({written['seconds']}s)")
    if bot is not None:
        print("telegram: sent (chart, text, Pine Script)")
    return 0


def ci_analyze(settings, text: str, archive: str, daily_limit: int) -> int:
    """GitHub Actions (analyst.yml): one requested analysis. The log is public: this
    prints one status line; the details go to the private log the workflow keeps."""
    from tascreen import notify
    from tascreen.analyst.request import handle_request
    from tascreen.store import Store

    bot = notify.from_environment()
    if bot is None:
        print("analysis: telegram is not configured")
        return 1
    store = Store(settings.data_dir)
    try:
        status = handle_request(text, bars_dir=store.bars_dir, read_bars=store.read_bars,
                                archive=Path(archive), bot=bot, make_llm=lambda: _analyst_llm(settings),
                                daily_limit=daily_limit)
    except Exception as exc:
        log.exception("the analysis failed")
        print(f"analysis: failed ({type(exc).__name__})")
        return 1
    print(f"analysis: {status}")
    return 0


def scan_symbol(settings, symbol: str) -> int:
    from tascreen.patterns.rules import load_rules
    from tascreen.scan import scan_symbol as scan_one
    from tascreen.store import Store

    bars = Store(settings.data_dir).read_bars(symbol)
    if bars is None:
        log.error("no stored bars for %s (run --bars first)", symbol)
        return 1
    rules = load_rules()
    values, found = scan_one(bars, symbol, rules)
    print(f"\n{symbol}: {values['bars']} bars to {values['last_date']}")
    for key in ("close", "rsi14", "sma50", "sma150", "atr_pct", "rel_volume", "pct_from_52w_high"):
        print(f"  {key:<18} {values[key]:.4g}" if values[key] == values[key] else f"  {key:<18} n/a")
    print(f"\n{len(found)} detection(s):")
    for det in found:
        spec = rules.pattern(det.pattern)
        print(f"\n  {spec.name_en} [{det.status}, {det.direction}] "
              f"{det.start.date()} -> {det.end.date()}"
              + (f", breakout {det.breakout_date.date()}" if det.breakout_date is not None else ""))
        for check in det.checks:
            mark = "ok " if check.passed else "NO "
            print(f"     {mark} {check.rule:<26} {check.value!s:<22} {check.threshold!s:<14} {check.origin}")
    print()
    return 0


def serve(settings) -> int:
    import threading
    import webbrowser

    import uvicorn

    from tascreen.web.app import HOST, create_app

    url = f"http://{HOST}:{settings.web.port}/"
    print(f"\nThe screener is at {url} (this computer only). Ctrl+C stops it.\n")
    if settings.web.open_browser:
        threading.Timer(1.5, webbrowser.open, (url,)).start()
    uvicorn.run(create_app(settings), host=HOST, port=settings.web.port, log_level="warning")
    return 0


def refresh_quotes(settings, client=None) -> dict:
    from datetime import datetime, timezone

    from tascreen.market_hours import live_session
    from tascreen.quotes import fetch_quotes
    from tascreen.store import Store

    client = client or make_tradingview(settings)
    frame, summary = client.with_session(lambda session: fetch_quotes(
        session, settings.universe, delays=settings.tradingview.rate_limit_delays))
    session = live_session(datetime.now(timezone.utc), market_tz=settings.market.timezone,
                           after_close_minutes=settings.live.after_close_minutes)
    summary["session"] = session.isoformat() if session else None
    Store(settings.data_dir).write_quotes(frame, summary)
    return summary


def quotes(settings) -> int:
    summary = refresh_quotes(settings)
    print(f"\nQuotes: {summary['rows']} stocks in {summary['seconds']:.0f} s "
          f"({summary['calls']} calls)"
          + ("" if summary["complete"] else f", {summary['missing']} missed (moved across a band edge)"))
    print(f"  session: {summary['session'] or 'none - the market is closed; prices are the last close'}\n")
    return 0


def live(settings) -> int:
    import time as clock
    from datetime import datetime, timedelta, timezone

    from tascreen.market_hours import live_session, next_open
    from tascreen.store import Store
    from tascreen.tv.data import RateLimited

    store, cfg, tz = Store(settings.data_dir), settings.live, settings.market.timezone
    client = make_tradingview(settings)
    writer = None
    if settings.channels.enabled:
        try:
            writer = make_channel_writer(settings)
        except ScreenerError as exc:
            # Inside Claude Code (CLAUDECODE=1) the agents cannot be run; quotes still are.
            log.warning("channels are off in this run: %s", exc)
    print(f"\nLive: every stock's price every {cfg.interval_minutes:g} min during the US session"
          + (", then the daily update after the close" if cfg.update_after_close else "")
          + (", with channel posts" if writer is not None else ", channels off")
          + ". Ctrl+C stops.\n")
    wait = cfg.interval_minutes          # grows while the screener keeps answering 429
    try:
        while True:
            now = datetime.now(timezone.utc)
            if live_session(now, market_tz=tz, after_close_minutes=cfg.after_close_minutes):
                started = clock.monotonic()
                try:
                    s = refresh_quotes(settings, client)
                    wait = cfg.interval_minutes
                    log.info("quotes: %d stocks, %d calls, %.0f s%s", s["rows"], s["calls"],
                             s["seconds"], "" if s["complete"] else f", {s['missing']} missed")
                    if writer is not None:
                        try:
                            live_channel_posts(settings, writer)
                        except ScreenerError as exc:
                            log.error("live channel posts failed: %s", exc)
                except RateLimited as exc:
                    # Knocking every few minutes on a scanner that answers 429 for hours
                    # only prolongs it: back off, doubling up to 30 minutes.
                    wait = min(wait * 2, max(30.0, cfg.interval_minutes))
                    log.warning("screener rate limited (%s); next try in %g min", exc, wait)
                except ScreenerError as exc:
                    log.error("quotes failed: %s", exc)
                clock.sleep(max(5.0, wait * 60 - (clock.monotonic() - started)))
                continue
            target = _target(settings)
            done_for = store.read_live_state().get("daily_update_for")
            if cfg.update_after_close and done_for != target.isoformat():
                deadline = next_open(now, market_tz=tz) - timedelta(minutes=10)
                log.info("daily update for %s (bars stop by %s)", target, deadline)
                try:
                    code = update(settings, None, stop_at=deadline)
                except ScreenerError as exc:
                    log.error("daily update failed: %s", exc)
                    code = 1
                if writer is not None:
                    try:
                        channels(settings, writer=writer)
                    except ScreenerError as exc:
                        log.error("daily channels failed: %s", exc)
                store.write_live_state({"daily_update_for": target.isoformat(), "exit_code": code,
                                        "finished_at": datetime.now(timezone.utc).isoformat(
                                            timespec="seconds")})
                continue
            wake = next_open(now, market_tz=tz)
            log.info("market closed; next open %s", wake.isoformat(timespec="minutes"))
            clock.sleep(min(1800.0, max(30.0, (wake - datetime.now(timezone.utc)).total_seconds())))
    except KeyboardInterrupt:
        print("\nLive stopped.\n")
        return 0


def make_channel_writer(settings):
    """ChannelWriter over Claude Code; ConfigError inside a Claude Code session."""
    from tascreen.channels.generate import ChannelWriter
    from tascreen.llm import ClaudeCodeLLM
    from tascreen.patterns.rules import load_rules
    from tascreen.store import Store

    cfg = settings.channels
    llm = ClaudeCodeLLM(model=cfg.model, effort=cfg.effort, timeout_s=cfg.timeout_s)
    return ChannelWriter(store=Store(settings.data_dir), rules=load_rules(), cfg=cfg, llm=llm)


def channels(settings, force: bool = False, writer=None) -> int:
    from tascreen.web.data import ScanRepository

    if not settings.channels.enabled:
        print("\nchannels.enabled is false in config.yaml\n")
        return 0
    writer = writer or make_channel_writer(settings)
    view = ScanRepository(writer.store, writer.rules).current()
    if view is None:
        log.error("no scan yet; run: .venv\\Scripts\\python.exe run.py --scan")
        return 1
    print(f"\nChannels for the session {view.day}: #כללי + {settings.channels.count} pattern channels, "
          f"written by Claude Code ({settings.channels.model}, effort {settings.channels.effort})\n")
    report = writer.daily(view, force=force)
    (settings.log_dir / "channels_last_run.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    failed = 0
    for channel, result in report["channels"].items():
        if isinstance(result, dict):
            print(f"  {channel:<22} {result['threads']} threads, {result['posts']} posts, "
                  f"{result['dropped']} dropped, {result['seconds']:.0f} s")
        else:
            print(f"  {channel:<22} {result}")
            failed += str(result).startswith("failed")
    print(f"  details: {settings.log_dir / 'channels_last_run.json'}\n")
    return 1 if failed else 0


def redraw_charts(settings) -> int:
    from tascreen.channels.generate import redraw_charts as redraw
    from tascreen.patterns.rules import load_rules
    from tascreen.store import Store
    from tascreen.web.data import ScanRepository

    store = Store(settings.data_dir)
    view = ScanRepository(store, load_rules()).current()
    if view is None:
        log.error("no scan yet")
        return 1
    counts = redraw(store, view)
    print(f"\nCharts: {counts['redrawn']} redrawn, {counts['kept']} kept (their pattern is no longer "
          "in the newest scan)\n")
    return 0


def live_channel_posts(settings, writer) -> None:
    """Threads about the crossings in the newest quotes (deduplicated, hourly cap)."""
    from datetime import datetime, timedelta, timezone

    from tascreen.web.data import QuotesRepository, ScanRepository, live_for

    view = ScanRepository(writer.store, writer.rules).current()
    max_age = timedelta(minutes=settings.live.interval_minutes * settings.live.stale_after_intervals)
    live = live_for(view, QuotesRepository(writer.store).current(), datetime.now(timezone.utc), max_age)
    if live is None or not live.active or not live.crossings:
        return
    result = writer.live(view, live.quotes.session, live.quotes.prices, live.quotes.changes,
                         live.crossings, progress=lambda m: log.info(m.strip()))
    if result["written"]:
        log.info("live channel threads: %d written", result["written"])


def update(settings, limit: int | None, stop_at=None) -> int:
    from tascreen.tv.data import RateLimited

    try:
        universe(settings)
    except RateLimited as exc:
        log.warning("screener rate limited (%s); trying the newest saved universe", exc)
        if usable_universe(settings, max_age_days=settings.universe.max_age_days) is None:
            return 1
    bars_code = bars(settings, limit, stop_at)   # a few failed symbols do not stop the scan
    return max(bars_code, scan(settings))


def _stop_at(max_minutes: float | None):
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) + timedelta(minutes=max_minutes) if max_minutes else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(Path(args.config) if args.config else None)
    setup_logging(settings.log_dir, verbose=args.verbose)

    commands = (
        (args.auth_tradingview, lambda: auth_tradingview(settings)),
        (args.tradingview_token_status, lambda: tradingview_token_status(settings)),
        (args.tradingview_diagnose, lambda: tradingview_diagnose(settings)),
        (args.tradingview_probe, lambda: tradingview_probe(args.tradingview_probe)),
        (args.tradingview_tools, lambda: tradingview_tools(settings)),
        (args.tradingview_call, lambda: tradingview_call(settings, args.tradingview_call)),
        (args.discover is not None, lambda: discover(settings, args.discover)),
        (args.universe, lambda: universe(settings)),
        (args.bars, lambda: bars(settings, args.limit, _stop_at(args.max_minutes))),
        (args.update, lambda: update(settings, args.limit, _stop_at(args.max_minutes))),
        (args.check_bars, lambda: check_bars(settings)),
        (args.scan, lambda: scan(settings)),
        (args.scan_symbol, lambda: scan_symbol(settings, args.scan_symbol.strip().upper())),
        (args.outcomes, lambda: outcomes(settings, rebuild=args.rebuild)),
        (args.backfill_outcomes, lambda: backfill_outcomes(settings, args.limit)),
        (args.ci_probe, lambda: ci_probe(settings, args.limit)),
        (args.ci_tick, lambda: ci_tick(settings, args.limit, args.max_minutes,
                                       with_channels=not args.no_channels)),
        (args.export_site, lambda: export_site(settings, args.export_site)),
        (args.setup_telegram, lambda: setup_telegram(settings)),
        (args.ci_notify, lambda: ci_notify(settings, args.ci_notify)),
        (args.analyze, lambda: analyze(settings, args.analyze, args.out, with_llm=not args.no_llm,
                                       to_telegram=args.telegram)),
        (args.ci_analyze, lambda: ci_analyze(settings, args.ci_analyze, args.archive, args.daily_limit)),
        (args.ci_live, lambda: ci_live(settings, args.max_minutes or 345)),
        (args.telegram_webhook, lambda: telegram_webhook(settings, args.telegram_webhook)),
        (args.quotes, lambda: quotes(settings)),
        (args.live, lambda: live(settings)),
        (args.channels, lambda: channels(settings, force=args.force)),
        (args.redraw_charts, lambda: redraw_charts(settings)),
        (args.serve, lambda: serve(settings)),
    )
    for requested, command in commands:
        if requested:
            try:
                return command()
            except ScreenerError as exc:
                log.error("%s", exc)
                return 1

    build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
