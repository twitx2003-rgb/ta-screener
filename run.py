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
        (args.bars, lambda: bars(settings, args.limit)),
        (args.update, lambda: update(settings, args.limit)),
        (args.check_bars, lambda: check_bars(settings)),
        (args.scan, lambda: scan(settings)),
        (args.scan_symbol, lambda: scan_symbol(settings, args.scan_symbol.strip().upper())),
        (args.outcomes, lambda: outcomes(settings, rebuild=args.rebuild)),
        (args.backfill_outcomes, lambda: backfill_outcomes(settings, args.limit)),
        (args.ci_probe, lambda: ci_probe(settings, args.limit)),
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
