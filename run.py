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
from tascreen.errors import ConfigError, ScreenerError
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
    parser.add_argument("--backtest", action="store_true",
                        help="Replay the breakout ledger as trades (entry next open; exit at the "
                             "target, the invalidation close or the last tracked session): win rate, "
                             "average return, profit factor per pattern -> data/outcomes/backtest.json")
    parser.add_argument("--backfill-outcomes", action="store_true",
                        help="Find past breakouts: the chart detector on stored bars, one past "
                             "session at a time without looking ahead (hours; resumable; "
                             "--limit N for the N largest stocks) -> the outcome ledger")
    parser.add_argument("--rebuild", action="store_true",
                        help="With --outcomes: build the ledger again from every saved scan "
                             "(and the saved backfill) made with the current rules")
    parser.add_argument("--backfill-needed", action="store_true",
                        help="Print yes if the saved backfill was made with other rules than "
                             "rules.yaml (or none exists), else no (backfill.yml)")
    parser.add_argument("--ci-probe", action="store_true",
                        help="Stage-0 check on a GitHub runner: TradingView (forced token refresh, "
                             "a screener pass, --limit N get-ohlcv calls, default 300) and one "
                             "Claude call; prints counts and timings only (public logs)")
    parser.add_argument("--ci-tick", action="store_true",
                        help="GitHub Actions: do whatever is due now (the daily update after a "
                             "session closes, then the evening alerts); logs/ci_summary.json gets counts "
                             "only, for the public log")
    parser.add_argument("--max-minutes", type=float, metavar="M",
                        help="With --update/--bars/--ci-tick: stop fetching bars after M minutes "
                             "(the rest are deferred to the next run); with --ci-live: start the "
                             "continuation after M minutes (default 345)")
    parser.add_argument("--export-webhook", metavar="DIR",
                        help="Write what Vercel serves to DIR: only the Telegram bot's webhook "
                             "(the website was removed)")
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
    parser.add_argument("--analyze-eval", nargs="?", const="", metavar="ROUND",
                        help="Analyse the fixed evaluation set (12 stocks in different situations) "
                             "-> logs/eval/<ROUND>/ for the review agents; nothing is sent "
                             "(normal terminal only)")
    parser.add_argument("--until", metavar="YYYY-MM-DD",
                        help="With --analyze-eval: the bars up to this day only (the day an earlier "
                             "round saw, so the rounds compare the analyst, not the market)")
    parser.add_argument("--telegram-webhook", metavar="URL",
                        help="Point the Telegram bot at the site's webhook (https://.../api/telegram/), "
                             "or 'off' to remove it")
    parser.add_argument("--ci-live", action="store_true",
                        help="GitHub Actions (live.yml): during the session, alert on prices "
                             "crossing a bullish pattern's breakout line (--max-minutes, then "
                             "the job starts its continuation)")
    parser.add_argument("--research-dry", action="store_true",
                        help="The research team on the newest scan -> Telegram as a trial (nothing "
                             "recorded); run.yml input research_dry, or a normal terminal")
    parser.add_argument("--digest", action="store_true",
                        help="The morning digest (10:00 Israel time) if it is due; the X news loop runs it")
    parser.add_argument("--digest-now", action="store_true",
                        help="The morning digest now, due or not (a trial; it still counts as today's)")
    parser.add_argument("--digest-trial", action="store_true",
                        help="The morning digest now, to the owner's private chat, not recorded "
                             "(xnews.yml input digest_trial)")
    parser.add_argument("--ensure-live", action="store_true",
                        help="GitHub Actions (xnews.yml): start live.yml if it should be running "
                             "(a trading day, 07:20-15:50 New York) and is not")
    parser.add_argument("--ci-premarket", action="store_true",
                        help="GitHub Actions (premarket.yml): the pre-market report when a slot "
                             "(07:30, 08:30, 09:15 New York) is due; one status line")
    parser.add_argument("--ci-analyze", metavar="SYMBOL",
                        help="GitHub Actions (analyst.yml): one requested analysis sent to Telegram, "
                             "kept under --archive, within --daily-limit")
    parser.add_argument("--archive", metavar="DIR", default="analyses",
                        help="With --ci-analyze: the analyses kept so far (one folder per UTC day)")
    parser.add_argument("--daily-limit", type=int, default=10,
                        help="With --ci-analyze: analyses per UTC day, at most")
    parser.add_argument("--quiet-unknown", action="store_true",
                        help="With --ci-analyze: no answer for an unknown symbol (a capital word "
                             "written alone in the group may be just a word)")
    parser.add_argument("--reply-to", metavar="CHAT", default="",
                        help="With --ci-analyze: answer in this chat, if it is the owner's private "
                             "chat or group (the webhook passes the chat it was asked in)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="With --bars/--update/--backfill-outcomes: only the N largest "
                             "symbols (a pilot run)")
    parser.add_argument("--setup-x", action="store_true",
                        help="Save the twitterapi.io key (asked for, never shown) and test it")
    parser.add_argument("--x-discover", metavar="ACCOUNT",
                        help="One real search for an X account: print the answer's keys (no post text)")
    parser.add_argument("--x-vision-check", action="store_true",
                        help="One real post picture -> Claude's explanation -> Telegram (confirms the "
                             "picture call; normal terminal or xnews.yml with check_vision)")
    parser.add_argument("--x-latest", type=int, nargs="?", const=60, metavar="MINUTES",
                        help="Print the accounts' posts of the last MINUTES (default 60), for the "
                             "news-analyst agent. Paid per post; this computer only, never in CI logs")
    parser.add_argument("--xnews", action="store_true",
                        help="One pass: new posts of xnews.accounts -> Claude picks -> Telegram "
                             "(normal terminal or xnews.yml)")
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
    # a universe saved under a lower floor (the owner raised it to $5B, 2026-09-28), and the
    # volume floor (2026-10-05): a stock trading too few shares a day is not followed
    keep = frame["market_cap"] >= settings.universe.min_market_cap
    if settings.universe.min_avg_volume > 0:
        keep &= frame["avg_volume_10d"] >= settings.universe.min_avg_volume
    return day, frame.loc[keep].reset_index(drop=True)


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
        backtest(settings, quiet=True)
    except (ScreenerError, OSError, ValueError) as exc:  # the scan itself is fine; the ledger waits
        log.error("outcome ledger or backtest not updated: %s", exc)
    print("  our indicators vs TradingView's:")
    for name, result in summary["cross_check_vs_tradingview"].items():
        if result.get("compared"):
            print(f"    {name:<8} {result['agree']}/{result['compared']} agree within "
                  f"{result['tolerance']} (median diff {result['median_diff']})")
    print()
    return 1 if summary["errors"] else 0


def outcomes(settings, rebuild: bool = False, quiet: bool = False) -> int:
    from tascreen.outcomes import update as update_outcomes
    from tascreen.patterns.rules import load_rules
    from tascreen.store import Store

    report = update_outcomes(Store(settings.data_dir), settings.outcomes.max_sessions,
                             rebuild=rebuild, rules_digest=load_rules().digest)
    line = (f"Outcomes: {report['rows']} breakouts tracked ({report['new']} new, "
            f"{report['restated']} restated) {report['outcomes']}")
    if quiet:
        log.info("%s", line)
    else:
        print(line)
    return 0


def backtest(settings, quiet: bool = False) -> int:
    """The outcome ledger replayed as trades (tascreen/backtest.py) -> data/outcomes/backtest.json,
    shown on the scorecard page. Statistics only, no symbols."""
    from datetime import datetime, timezone

    from tascreen import backtest as bt
    from tascreen.patterns.rules import load_rules
    from tascreen.store import Store

    store = Store(settings.data_dir)
    done = bt.trades(store.read_ledger(), store.read_bars)
    rows = bt.summary(done, settings.outcomes.min_cases)
    store.write_backtest({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "period": bt.period(done), "trades": int(len(done)),
                          "min_trades": settings.outcomes.min_cases, "rows": rows})
    if quiet:
        log.info("backtest: %d trades, %d rows", len(done), len(rows))
        return 0
    names = {k: v.name_he for k, v in {**load_rules().chart, **load_rules().candle}.items()}
    print(f"\nBacktest: {len(done):,} trades {bt.period(done)} (entry: next open; exit: target, "
          "invalidation close or last tracked session; no costs)\n")
    for r in rows:
        name = "all" if r["pattern"] == "all" else r["pattern"]
        print(f"  {name:<22} {r['direction']:<8} n={r['trades']:>6,} win {r['win_pct']:>5}%  "
              f"avg {r['avg_return_pct']:>6}%  median {r['median_return_pct']:>6}%  "
              f"PF {r['profit_factor']}  days {r['avg_sessions']}  against {r['median_mae_pct']}%/{r['p90_mae_pct']}%")
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


def backfill_needed(settings) -> int:
    """yes / no on stdout: the breakout history is out of date when rules.yaml changed
    since the saved backfill was made, or a backfill with these rules never finished (a
    run cut short resumes where it stopped). backfill.yml asks every Saturday."""
    import json

    from tascreen.patterns.rules import load_rules
    from tascreen.store import Store

    digest = load_rules().digest
    saved = Store(settings.data_dir).read_backfill_manifest().get("rules_digest")
    try:
        finished = json.loads((settings.log_dir / "backfill_last_run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        finished = {}
    print("yes" if saved != digest or finished.get("rules_digest") != digest else "no")
    return 0


def ci_probe(settings, calls: int | None) -> int:
    from tascreen.ci import probe
    from tascreen.llm import ClaudeCodeLLM

    report = probe(settings, make_tradingview(settings),
                   lambda: ClaudeCodeLLM(model=settings.claude.model, effort="low", timeout_s=180),
                   calls=calls or 300)
    print(json.dumps(report, indent=2))
    return 0 if report["tradingview"].get("ok") and report["claude"].get("ok") else 1


def ci_tick(settings, limit: int | None, max_minutes: float | None) -> int:
    """What is due now on a GitHub runner. The daily update runs when the last completed
    session has no complete update yet (a run cut short, e.g. by a throttled TradingView,
    is finished by the next one); then the evening alerts. The public log shows only
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
        store.write_live_state({"daily_update_for": target.isoformat(), "complete": complete,
                                "exit_code": code,
                                "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        summary["complete"] = complete
    if settings.alerts.enabled:
        summary["alerts"] = _evening_alerts(settings, store, target)
    if settings.setups_list.enabled:
        summary["setups_list"] = _setups_list(settings, store, target)
    if settings.research.enabled:
        summary["research_weekly"] = _research_weekly(settings, store)
    summary["market"] = _market_snapshot(settings, store, target)
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
    live = _read_log_json(settings, "live_summary.json")         # the session's watch, if it ran
    if not str(live.get("started", "")).startswith(target.isoformat()):
        live = {}
    view = ScanRepository(store, load_rules()).current()
    can_dispatch = bool(os.environ.get("GH_DISPATCH_TOKEN", "").strip())

    def regular(note: str = "") -> dict:
        return evening_report(store, view, settings.alerts, bot=bot, min_cases=settings.outcomes.min_cases,
                              dispatch=github.dispatch, can_dispatch=can_dispatch, live_summary=live,
                              news_of=lambda symbols: _news(settings, symbols), images=True, note=note)

    try:
        if not settings.research.enabled:
            return regular()
        from tascreen import research

        return research.evening_report(store, view, settings.alerts, bot=bot, min_cases=settings.outcomes.min_cases,
                                       run=_research_runner(settings, store, view), dispatch=github.dispatch,
                                       can_dispatch=can_dispatch, fallback=regular, live_summary=live,
                                       recent_days=settings.research.recent_days)
    except ScreenerError as exc:
        log.error("breakout report failed: %s", exc)             # the private log only
        return {"status": "failed", "error": type(exc).__name__}


def _setups_list(settings, store, target, client=None, owner=None) -> dict:
    """The bot's own TradingView list of the owner's setups (tascreen/setups_list.py), once per
    session, after the evening report: the chart analyst over every stock the scan covers.
    Counts only: this goes to the public log."""
    from tascreen import alerts, notify, setups_list
    from tascreen.analyst import load_rules as analyst_rules
    from tascreen.patterns.rules import load_rules
    from tascreen.web.data import ScanRepository

    cfg = settings.setups_list
    if not cfg.id:
        return {"status": "no list id"}
    if cfg.id == settings.watchlist.id:
        return {"status": "refused: that is the owner's own list"}
    if not alerts.read_sent(store, target).get("evening"):
        return {"status": "waiting for the evening report"}
    state = setups_list.read(store.root)
    if state.get("id") != cfg.id:
        state = {}
    if state.get("day") != target.isoformat() or state.get("version") != setups_list.VERSION:
        view = ScanRepository(store, load_rules()).current()
        if view is None or view.day != target:
            return {"status": "no scan of the session yet"}
        found, counts = setups_list.find_setups(store.root, list(view.stocks["symbol"]), cfg)
        log.info("setups list: %s", counts)
        state = setups_list.refresh(state, found, view, store.read_bars, cfg,
                                    break_atr=float(analyst_rules()["support_break_atr"]))
        setups_list.write(store.root, state)
        text = setups_list.message(state)
        owner = owner or notify.owner_from_environment()
        if text and owner is not None:
            owner.send(text, html=True)
    out = {"entries": len(state["entries"]), "changes": len(state.get("events") or [])}
    if state.get("synced"):
        return {**out, "status": "kept"}
    client = client or make_tradingview(settings)
    wanted = [e["symbol"] for e in state["entries"]]

    async def work():
        async with client.own_list_session(cfg.id, setups_list.NAME) as session:
            return await setups_list.sync(session, cfg.id, wanted, state.get("managed") or [],
                                          settings.tradingview.rate_limit_delays)
    try:
        from tascreen.tv.mcp_client import _run

        counts = _run(work())
    except (ScreenerError, OSError, TimeoutError, ExceptionGroup) as exc:
        log.warning("the setups list was not synced: %s", type(exc).__name__)
        return {**out, "status": "not synced", "error": type(exc).__name__}
    setups_list.write(store.root, {**state, "synced": True})
    return {**out, **counts, "status": "synced"}


def _market_snapshot(settings, store, day) -> str:
    """The index funds' moves in the session just scanned, kept for the morning digest
    (data/market/<day>.json; tascreen/digest.py). Once per session; a status word."""
    path = store.root / "market" / f"{day.isoformat()}.json"
    if path.exists():
        return "kept"
    moves = _market_moves(settings)
    if not moves:
        return "not fetched"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(moves), encoding="utf-8")
    return "saved"


def _saved_holdings(settings) -> list[str]:
    """The owner's watchlist tickers for the X news (tascreen/watchlist.py): the live
    watch's file in the private state repo, or the local one; [] when off or unknown."""
    import os

    from tascreen import explain, watchlist

    if not settings.watchlist.enabled or not settings.watchlist.id:
        return []
    kept = watchlist.read(settings.data_dir)
    if not kept and os.environ.get("STATE_REPO_TOKEN", "").strip():
        raw = explain._get(f"https://api.github.com/repos/{explain.STATE_REPO}/contents/data/watchlist.json?ref=main",
                           {"Authorization": f"Bearer {os.environ['STATE_REPO_TOKEN'].strip()}",
                            "Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28",
                            "User-Agent": "ta-screener"})
        try:
            kept = json.loads(raw or b"{}")
        except ValueError:
            kept = {}
    return watchlist.tickers(list(kept.get("symbols") or [])) if kept.get("id") == settings.watchlist.id else []


def _private_sender():
    """Sends HTML to the owner's private chat, or None without a bot."""
    from tascreen import notify

    owner = notify.owner_from_environment()
    return (lambda text: owner.send(text, html=True)) if owner is not None else None


def _saved_moves(settings, day) -> dict:
    """The morning digest's index moves: the nightly run's file, from the private state repo
    (the news loop has no bars or scans) or, on this computer, the local one; {} if none."""
    import os

    from tascreen import explain

    local = settings.data_dir / "market" / f"{day.isoformat()}.json"
    raw = local.read_bytes() if local.exists() else None
    token = os.environ.get("STATE_REPO_TOKEN", "").strip()
    if raw is None and token:
        raw = explain._get(f"https://api.github.com/repos/{explain.STATE_REPO}/contents/data/market/"
                           f"{day.isoformat()}.json?ref=main",
                           {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json",
                            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ta-screener"})
    try:
        moves = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return {k: float(v) for k, v in moves.items() if isinstance(v, (int, float))} if isinstance(moves, dict) else {}


def digest(settings, force: bool = False, trial: bool = False) -> int:
    """The morning digest (tascreen/digest.py) when it is due, or now with `force`. From the
    X news loop (.github/workflows/xnews.yml): it has Claude, the news state and the group.
    A `trial` goes to the owner's private chat and is not recorded. One status line (counts
    only: the public log)."""
    import os
    from datetime import datetime, timezone

    from tascreen import digest as morning, notify
    from tascreen.digest_render import to_png
    from tascreen.llm import ClaudeCodeLLM

    bot = notify.from_environment()
    if bot is None:
        print("digest: status=telegram not configured")
        return 0
    cfg, folder = settings.xnews, settings.data_dir / "xnews"
    record = settings.log_dir / "digest-trial.json" if trial else folder / "digest.json"
    if trial:
        record.unlink(missing_ok=True)
        bot.chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or str(bot.chat_id)).strip()
    try:
        result = morning.run(
            now=datetime.now(timezone.utc), state_path=folder / "state.json", record_path=record,
            market_tz=settings.market.timezone,
            make_llm=lambda: ClaudeCodeLLM(model=cfg.model, effort="medium", timeout_s=cfg.timeout_s),
            send_photo=lambda png, caption: bot.send_photo(png, caption, "digest.png", html=True),
            moves_of=lambda day: _saved_moves(settings, day), draw=to_png, folder=settings.log_dir / "digest",
            force=force or trial)
    except Exception as exc:                        # the loop goes on; the next pass tries again
        log.exception("the morning digest failed")
        print(f"digest: status=failed ({type(exc).__name__})")
        return 1
    print("digest: " + " ".join(f"{k}={v}" for k, v in result.items()))
    return 0


def _market_moves(settings) -> dict:
    """The index funds' session moves for the research team; {} if TradingView fails."""
    from tascreen import explain

    try:
        indexes = make_tradingview(settings).with_session(
            lambda session: explain.fetch_indexes(session, settings.tradingview.rate_limit_delays))
        return explain.moves(indexes, premarket=False)
    except (ScreenerError, OSError, TimeoutError, ExceptionGroup) as exc:
        log.warning("index funds for the research: %s", type(exc).__name__)
        return {}


def _research_runner(settings, store, view):
    """research.research bound to this run's Claude, news, market and analyst engine."""
    from tascreen import research
    from tascreen.analyst.facts import analyse
    from tascreen.llm import ClaudeCodeLLM

    cfg = settings.research

    def run(breakouts, verge):
        from tascreen import alerts

        llm = ClaudeCodeLLM(model=cfg.model, effort=cfg.effort, timeout_s=cfg.timeout_s)
        proven = alerts.proven_patterns(store.read_ledger(), settings.outcomes.min_cases,
                                        settings.alerts.min_success_pct)
        return research.research(view, store, breakouts, verge, llm=llm, verge_pct=settings.alerts.verge_pct,
                                 shortlist_size=cfg.shortlist, max_picks=cfg.max_picks, patterns=proven,
                                 min_picks=cfg.min_picks,
                                 news_of=lambda symbols: _news(settings, symbols),
                                 market=_market_moves(settings), analyse=analyse)
    return run


def _research_weekly(settings, store) -> dict:
    """Once a week (from Saturday's runs): the coach reviews the picks, rewrites the
    lessons, and the owner gets the track record. Counts only in the return."""
    from datetime import datetime, timezone

    from tascreen import notify, research
    from tascreen.llm import ClaudeCodeLLM

    now = datetime.now(timezone.utc)
    if now.weekday() != 5 or not research.weekly_due(store, now):
        return {"status": "not due"}
    if not research.read_picks(store):
        return {"status": "no picks yet"}
    bot = notify.from_environment()
    if bot is None:
        return {"status": "telegram not configured"}
    cfg = settings.research
    try:
        review = research.weekly_review(store, ClaudeCodeLLM(model=cfg.model, effort=cfg.effort,
                                                             timeout_s=cfg.timeout_s), now)
    except ScreenerError as exc:
        log.error("weekly research review failed: %s", exc)
        return {"status": "failed", "error": type(exc).__name__}
    bot.send(research.weekly_message(review), html=True)
    return {"status": "sent", **review["counts"], "lessons": review["lessons"]}


def research_dry(settings) -> int:
    """The research team on the newest scan, sent to Telegram marked as a trial (nothing
    recorded, no analyses started). One status line."""
    from tascreen import alerts, notify, research
    from tascreen.patterns.rules import load_rules
    from tascreen.store import Store
    from tascreen.web.data import ScanRepository

    store = Store(settings.data_dir)
    view = ScanRepository(store, load_rules()).current()
    if view is None:
        print("research-dry: no scan")
        return 1
    rates = alerts.hit_rates(store.read_ledger(), settings.outcomes.min_cases)
    breakouts = alerts.bullish_breakouts(view, store.read_bars, rates)
    verge = [{**v, "hit_rate": rates.get(v["pattern"])}
             for v in alerts.on_the_verge(view, settings.alerts.watch_pct)]
    result = _research_runner(settings, store, view)(breakouts, verge)
    bot = notify.from_environment()
    if bot is not None and result["status"] in ("picked", "none"):
        for message in research.report_messages(view.day, result):
            bot.send("🧪 ניסיון של צוות המחקר (לא נשמר)\n\n" + message, html=True)
    print(f"research-dry: status={result['status']} candidates={result['candidates']} "
          f"shortlist={len(result['shortlist'])} picks={len(result['picks'])} dropped={len(result['dropped'])}")
    return 0


def _news(settings, symbols: list[str], client=None) -> dict:
    """Headlines for the alerts (tascreen/alerts.py); none at all if TradingView fails."""
    from datetime import datetime, timezone

    from tascreen import alerts

    try:
        client = client or make_tradingview(settings)
        return client.with_session(lambda session: alerts.fetch_news(
            session, symbols, datetime.now(timezone.utc)))
    except (ScreenerError, OSError, TimeoutError, ExceptionGroup) as exc:
        log.warning("news for the alerts failed: %s", type(exc).__name__)
        return {}


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
        if cfg.premarket and timedelta(minutes=15) < opens - started <= timedelta(minutes=PREMARKET_LEAD_MINUTES):
            # the pre-market reports, in this same run: one TradingView sign-in at a time
            summary["premarket"] = premarket_until_open(settings, opens)
        elif opens - started > timedelta(minutes=15):
            return finish("the market is closed")        # the other start time covers winter/summer
        clock.sleep(max(0.0, (opens - datetime.now(timezone.utc)).total_seconds()))
        day = live_session(datetime.now(timezone.utc), market_tz=tz, after_close_minutes=after)
    store = Store(settings.data_dir)
    view = ScanRepository(store, load_rules()).current()
    if view is None:
        return finish("no scan")
    proven = alerts.proven_patterns(store.read_ledger(), settings.outcomes.min_cases, cfg.min_success_pct)
    near, far = alerts.watch_tiers(view, cfg.verge_pct, cfg.watch_pct, cfg.live_max_symbols, proven)
    client = make_tradingview(settings)
    holdings = _holdings(settings, store, client, day)      # priced every pass
    near = near + [s for s in holdings if s not in near]
    far = [s for s in far if s not in holdings]
    owner = notify.owner_from_environment() or bot
    summary.update(watched=len(near) + len(far), watched_near=len(near), watched_far=len(far),
                   holdings=len(holdings), holding_alerts=0, data_delay_min=None)
    if not near and not far:
        return finish("nothing near a breakout line")
    delays: list[float] = []
    ends = session_bounds(day, tz)[1] + timedelta(minutes=after)
    hand_over = started + timedelta(minutes=max_minutes)
    interval = cfg.live_interval_minutes
    sent = alerts.read_sent(store, day)
    if not sent.get("watch_started"):           # once a session: the owner knows it runs
        bot.send(alerts.watch_started_message(len(near), len(far), cfg), html=True)
        sent["watch_started"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        alerts.write_sent(store, day, sent)
        push_token_file(alerts.sent_path(store, day), "live watch started")
    all_seconds: list[float] = []
    index_history: list = []                 # (time, {ticker: price}) for the hourly move
    while True:
        now = datetime.now(timezone.utc)
        if now >= ends:
            return finish("the session is over")
        if now >= hand_over:
            status = github.dispatch("live.yml", {"continued": "yes"})
            return finish(f"handed over ({status})", 0 if status == 204 else 1)
        pass_started = clock.monotonic()
        # the stocks near their line every pass; the farther ones every `far_every`-th
        watch = near + (far if summary["passes"] % cfg.far_every == 0 else [])
        try:
            got = client.with_session(lambda session: alerts.fetch_live_prices(
                session, watch, day, tz, concurrency=settings.bars.concurrency, measure_delay=True))
        except (ProviderError, OSError, TimeoutError, ExceptionGroup) as exc:
            log.warning("a price pass failed: %s", type(exc).__name__)
            summary["failed_passes"] += 1
            got = {"prices": {}, "seconds": [], "failed": 0, "delay_min": None}
        summary["passes"] += 1
        if got.get("delay_min") is not None:
            delays.append(got["delay_min"])
            summary["data_delay_min"] = round(statistics.median(delays), 1)
        summary["calls"] += len(got["seconds"]) + got["failed"]
        summary["failed_calls"] += got["failed"]
        all_seconds += got["seconds"]
        if all_seconds:
            summary["median_call_s"] = round(statistics.median(all_seconds), 2)
        if settings.explain.enabled:
            _session_move(settings, client, bot, store, day, sent, index_history, summary)
        found = alerts.live_crossings(view, got["prices"], day, sent.get("live", {}), patterns=proven,
                                      min_above_pct=cfg.live_min_above_pct, per_pattern=cfg.live_per_pattern)
        if found:
            symbols, at = [c["symbol"] for c in found], datetime.now(timezone.utc)
            news = _news(settings, symbols, client)
            try:                                    # the watch restores no bars: fetch the few needed
                bars = client.with_session(lambda session: alerts.fetch_bars(session, symbols, day))
            except (ScreenerError, OSError, TimeoutError, ExceptionGroup):
                bars = {}
            photos = alerts.crossing_photos(found, bars.get, at, tz, news)
            try:
                if len(photos) == len(found):       # each crossing with its pattern's chart
                    bot.send_album(photos)
                else:
                    bot.send(alerts.live_message(found, at, tz, news), html=True)
                    if photos:
                        bot.send_album(photos)
            except (ScreenerError, OSError):
                bot.send(alerts.live_message(found, at, tz, news), html=True)
            summary["charts"] = summary.get("charts", 0) + len(photos)
            live = sent.setdefault("live", {})
            for c in found:
                live[c["key"]] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "price": c["price"], "line": c["line"]}
            alerts.write_sent(store, day, sent)
            push_token_file(alerts.sent_path(store, day), "live alerts sent")
            summary["alerts"] += len(found)
        if holdings:
            _holding_alerts(settings, owner, client, store, view, got, holdings, day, sent, summary)
        if settings.setups_list.enabled and "setups_open" not in summary and datetime.now(timezone.utc) >= (
                session_bounds(day, tz)[0] + timedelta(minutes=settings.setups_list.open_after_minutes)):
            summary["setups_open"] = _setups_open(settings, client, store, day, owner)
        # TradingView slows down after heavy use: a slow pass stretches the interval
        if got["seconds"] and statistics.median(got["seconds"]) > 5:
            interval = min(interval * 2, 60.0)
        wait = pass_started + interval * 60 - clock.monotonic()
        limit = min(ends, hand_over) - datetime.now(timezone.utc)
        clock.sleep(max(0.0, min(wait, limit.total_seconds())))


def _setups_open(settings, client, store, day, owner) -> dict:
    """The setups list at the open (tascreen/setups_list.py open_review; owner, 2026-10-06):
    once a session, the prices of the list and of last night's pool, the entries already
    under their level out, the pool's best in. Counts only: this goes to the public log."""
    from tascreen import alerts, setups_list
    from tascreen.analyst import load_rules as analyst_rules
    from tascreen.tv.mcp_client import _run, push_token_file

    cfg = settings.setups_list
    state = setups_list.read(store.root)
    if not cfg.id or cfg.id == settings.watchlist.id or state.get("id") != cfg.id:
        return {"status": "no list"}
    if state.get("version") != setups_list.VERSION or state.get("open_day") == day.isoformat():
        return {"status": "not due"}
    symbols = sorted({e["symbol"] for e in state.get("entries") or []} | {c["symbol"] for c in state.get("pool") or []})
    try:
        got = client.with_session(lambda session: alerts.fetch_live_prices(
            session, symbols, day, settings.market.timezone, concurrency=settings.bars.concurrency))
    except (ScreenerError, OSError, TimeoutError, ExceptionGroup) as exc:
        log.warning("the setups list's open prices failed: %s", type(exc).__name__)
        return {"status": "no prices", "error": type(exc).__name__}
    state = setups_list.open_review(state, got["prices"], cfg, day=day.isoformat(),
                                    break_atr=float(analyst_rules()["support_break_atr"]))
    setups_list.write(store.root, state)
    push_token_file(setups_list.path(store.root), "setups list at the open")
    text = setups_list.message(state, at_open=True)
    if text:
        owner.send(text, html=True)
    out = {"entries": len(state["entries"]), "changes": len(state["events"])}
    wanted = [e["symbol"] for e in state["entries"]]

    async def work():
        async with client.own_list_session(cfg.id, setups_list.NAME) as session:
            return await setups_list.sync(session, cfg.id, wanted, state.get("managed") or [],
                                          settings.tradingview.rate_limit_delays)
    try:
        counts = _run(work())
    except (ScreenerError, OSError, TimeoutError, ExceptionGroup) as exc:
        log.warning("the setups list was not synced at the open: %s", type(exc).__name__)
        return {**out, "status": "not synced", "error": type(exc).__name__}
    setups_list.write(store.root, {**state, "synced": True})
    push_token_file(setups_list.path(store.root), "setups list synced at the open")
    return {**out, **counts, "status": "synced"}


def _holdings(settings, store, client, day) -> list[str]:
    """The owner's watchlist symbols (tascreen/watchlist.py): read from TradingView once a
    trading day and kept in data/watchlist.json; the kept list if TradingView fails."""
    from tascreen import watchlist

    cfg = settings.watchlist
    if not cfg.enabled or not cfg.id:
        return []
    kept = watchlist.read(store.root)
    if kept.get("day") == day.isoformat() and kept.get("id") == cfg.id:
        return list(kept.get("symbols") or [])
    try:
        name, symbols = client.with_session(
            lambda session: watchlist.fetch(session, cfg.id, settings.tradingview.rate_limit_delays))
    except (ScreenerError, OSError, TimeoutError, ExceptionGroup) as exc:
        log.warning("the watchlist was not read: %s", type(exc).__name__)
        return list(kept.get("symbols") or [])
    watchlist.write(store.root, {"id": cfg.id, "name": name, "symbols": symbols, "day": day.isoformat()})
    return symbols


def _holding_alerts(settings, owner, client, store, view, got, holdings, day, sent, summary) -> None:
    """The watchlist's alerts of one pass, to the owner's private chat: any of its patterns
    crossing a line (not already told to the group), and a move of another move_pct."""
    from datetime import datetime, timezone

    from tascreen import alerts, watchlist
    from tascreen.tv.mcp_client import push_token_file

    cfg, tz = settings.alerts, settings.market.timezone
    mine = {s: p for s, p in got["prices"].items() if s in holdings}
    if not mine:
        return
    told = {**sent.get("live", {}), **sent.get("holding_live", {})}
    found = alerts.live_crossings(view, mine, day, told, min_above_pct=cfg.live_min_above_pct)
    changed = False
    if found:
        at = datetime.now(timezone.utc)
        news = _news(settings, [c["symbol"] for c in found], client)
        owner.send(watchlist.CROSSING_HEAD + "\n" + alerts.live_message(found, at, tz, news), html=True)
        for c in found:
            sent.setdefault("holding_live", {})[c["key"]] = {"at": at.isoformat(timespec="seconds"),
                                                             "price": c["price"], "line": c["line"]}
        summary["holding_alerts"] += len(found)
        changed = True
    moved = sent.setdefault("holding_moves", {})
    moves = watchlist.new_moves(mine, got.get("closes") or {}, moved, settings.watchlist.move_pct)
    if moves:
        owner.send(watchlist.move_message(moves), html=True)
        for m in moves:
            moved.setdefault(m["symbol"], {})[m["side"]] = m["level"]
        summary["holding_alerts"] += len(moves)
        changed = True
    if changed:
        alerts.write_sent(store, day, sent)
        push_token_file(alerts.sent_path(store, day), "watchlist alerts sent")


def _session_move(settings, client, bot, store, day, sent, history, summary) -> None:
    """One index-funds call per pass; on a sharp SPY/QQQ move, the explainer's message
    (tascreen/explain.py move_due). Failures only skip this pass."""
    from datetime import datetime, timedelta, timezone

    from tascreen import alerts, explain

    cfg = settings.explain
    try:
        indexes = client.with_session(
            lambda session: explain.fetch_indexes(session, settings.tradingview.rate_limit_delays))
    except (ScreenerError, OSError, TimeoutError, ExceptionGroup) as exc:
        log.warning("index funds not fetched: %s", type(exc).__name__)
        return
    now = datetime.now(timezone.utc)
    done = sent.setdefault("explain", [])
    reason = explain.move_due(now, indexes, history, done, day_pct=cfg.day_move_pct,
                              hour_pct=cfg.hour_move_pct, max_per_day=cfg.max_per_day,
                              min_gap_minutes=cfg.min_gap_minutes)
    history.append((now, {s.split(":")[-1]: v["close"] for s, v in indexes.items()}))
    del history[:-30]
    if reason is None:
        return
    why = _explain(settings, "session", indexes, [], now)
    bot.send(explain.move_message(indexes, why), html=True)
    done.append({"at": now.isoformat(timespec="seconds"), "reason": reason,
                 "moves": explain.moves(indexes, premarket=False)})
    alerts.write_sent(store, day, sent)
    summary["explained_moves"] = summary.get("explained_moves", 0) + 1


PREMARKET_LEAD_MINUTES = 135      # live.yml starts at 07:25 New York; the open is 09:30


PREMARKET_RETRY_MINUTES = 5


def premarket_until_open(settings, opens, sleep=None, now=None) -> list[str]:
    """The pre-market slots until the open (tascreen/premarket.py SLOTS), each sent once;
    returns each attempt's status. A failed report is logged and tried again every few
    minutes while its slot lasts (2026-09-29: the 07:30 report was lost to one failure;
    a slot already sent, or over, is a no-op)."""
    import time as clock
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from tascreen.premarket import SLOTS

    sleep, now = sleep or clock.sleep, now or (lambda: datetime.now(timezone.utc))
    tz = ZoneInfo(settings.market.timezone)
    statuses = []
    while now() < opens - timedelta(minutes=1):
        failed = False
        try:
            code = ci_premarket(settings)
            statuses.append("ok" if code == 0 else f"exit {code}")
            failed = code != 0
        except (ScreenerError, OSError, TimeoutError, ExceptionGroup) as exc:
            log.error("pre-market report failed: %s", exc)            # the private log only
            statuses.append(type(exc).__name__)
            failed = True
        local = now().astimezone(tz)
        starts = [datetime.combine(local.date(), s, tz) for s in SLOTS]
        wake = min([t for t in starts if t > local] + [opens])
        if failed:
            wake = min(wake, local + timedelta(minutes=PREMARKET_RETRY_MINUTES))
        if wake >= opens:
            break
        sleep(max(1.0, (wake - now()).total_seconds() + 5))
    return statuses


def ensure_live(settings, now=None) -> int:
    """GitHub's schedule is not reliable (2026-09-28: nothing fired for two days), so the X
    news loop checks every pass that the live watch (pre-market reports, then the session)
    runs when it should, and starts it if not. One status line."""
    from datetime import datetime, time as clock_time, timezone
    from zoneinfo import ZoneInfo

    from tascreen import github
    from tascreen.market_hours import is_trading_day

    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(settings.market.timezone))
    if not settings.alerts.enabled or not is_trading_day(local.date()) \
            or not clock_time(7, 20) <= local.time() < clock_time(15, 50):
        print("ensure-live: not needed now")
        return 0
    active = github.active_runs("live.yml")
    if active is None:
        print("ensure-live: unknown (no key or no answer)")
        return 0
    if active:
        print("ensure-live: running")
        return 0
    status = github.dispatch("live.yml", {"continued": ""})
    print(f"ensure-live: started ({status})")
    return 0 if status == 204 else 1


def _explain(settings, moment: str, indexes: dict, movers: list, now) -> str | None:
    """The market explainer's sentences (tascreen/explain.py), or None: off, no Claude here
    (inside Claude Code), or an answer that broke a rule. Never stops a report."""
    from tascreen import explain
    from tascreen.llm import ClaudeCodeLLM

    cfg = settings.explain
    if not cfg.enabled:
        return None
    try:
        llm = ClaudeCodeLLM(model=cfg.model, effort=cfg.effort, timeout_s=cfg.timeout_s)
        news = explain.recent_news(now, hours=cfg.news_hours)
        return explain.explain(llm, moment=moment, indexes=indexes, movers=movers, news=news)
    except (ScreenerError, OSError) as exc:
        log.warning("market explainer: %s", type(exc).__name__)
        return None


def ci_premarket(settings) -> int:
    """The pre-market report (tascreen/premarket.py), once per slot. The public log gets
    one line of counts; the report goes to Telegram."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from tascreen import alerts, notify, premarket
    from tascreen.patterns.rules import load_rules
    from tascreen.store import Store
    from tascreen.web.data import ScanRepository

    cfg, tz = settings.alerts, settings.market.timezone
    bot = notify.from_environment()
    if bot is None or not cfg.enabled or not cfg.premarket:
        print("premarket: status=" + ("telegram not configured" if bot is None else "off"))
        return 0
    store, now = Store(settings.data_dir), datetime.now(timezone.utc)
    sent = alerts.read_sent(store, now.astimezone(ZoneInfo(tz)).date())   # today's session in New York
    day, slot = premarket.slot_due(now, tz, sent.get("premarket", {}))
    if day is None:
        print(f"premarket: status={slot}")
        return 0
    from tascreen import explain

    async def fetch(session):
        delays = settings.tradingview.rate_limit_delays
        movers = await premarket.fetch_movers(session, settings.universe, delays,
                                              min_pct=cfg.premarket_min_pct, min_volume=cfg.premarket_min_volume)
        try:
            indexes = await explain.fetch_indexes(session, delays)
        except (ScreenerError, OSError, TimeoutError) as exc:
            log.warning("index funds not fetched: %s", type(exc).__name__)
            indexes = {}
        return movers, indexes

    movers, indexes = make_tradingview(settings).with_session(fetch)
    crossings = []
    view = ScanRepository(store, load_rules()).current()
    if view is not None and movers["up"]:
        proven = alerts.proven_patterns(store.read_ledger(), settings.outcomes.min_cases, cfg.min_success_pct)
        crossings = alerts.live_crossings(view, {m["symbol"]: m["price"] for m in movers["up"]}, day,
                                          sent.get("premarket_crossings", {}), patterns=proven)
    why = _explain(settings, "pre-market", indexes, movers["up"] + movers["down"], now) if indexes else None
    bot.send(premarket.message(slot, movers, crossings, cfg.premarket_min_pct,
                               index_line=explain.index_line(indexes, premarket=True), why=why), html=True)
    sent.setdefault("premarket", {})[slot] = now.isoformat(timespec="seconds")
    for c in crossings:
        sent.setdefault("premarket_crossings", {})[c["key"]] = slot
    alerts.write_sent(store, day, sent)
    print(f"premarket: status=sent slot={slot} up={len(movers['up'])} down={len(movers['down'])} "
          f"stale={movers['stale']} crossings={len(crossings)} indexes={len(indexes)} why={why is not None}")
    return 0


def _read_log_json(settings, name: str) -> dict:
    path = settings.log_dir / name
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def export_webhook(out: str) -> int:
    from tascreen.web.export import export_webhook as export

    report = export(Path(out))
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
    text = run_message(_read_log_json(settings, "ci_summary.json"), status, run_url)
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

    cfg = settings.claude
    return ClaudeCodeLLM(model=cfg.model, effort=writer.EFFORT, timeout_s=cfg.timeout_s)


def analyze(settings, text: str, out: str | None, with_llm: bool, to_telegram: bool) -> int:
    from tascreen import notify
    from tascreen.analyst import find_symbol
    from tascreen.analyst.request import company_name, produce
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
    done = produce(symbol, store.read_bars(symbol), folder, llm=llm, bot=bot,
                   name=company_name(store.scans_dir, symbol, store.universe_dir))
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


def analyze_eval(settings, round_name: str | None, until: str | None = None) -> int:
    """The evaluation set (tascreen/analyst/evaluate.py) -> logs/eval/<round>/, for the
    review agents. Normal terminal only (Claude Code writes the texts); nothing is sent."""
    from tascreen.analyst.evaluate import EVAL_SET, run_eval
    from tascreen.analyst.png import svg_to_png
    from tascreen.store import Store

    folder = settings.log_dir / "eval" / (round_name or datetime_stamp())
    print(f"\nEvaluation set: {len(EVAL_SET)} analyses -> {folder} (about a minute each)\n")
    index = run_eval(Store(settings.data_dir), _analyst_llm(settings), folder, to_png=svg_to_png, until=until)
    failed = [s["symbol"] for s in index["stocks"] if s.get("error")]
    print(f"\ndone: {len(index['stocks']) - len(failed)} analyses"
          + (f", failed: {', '.join(failed)}" if failed else "") + f" -> {folder / 'index.json'}")
    return 1 if failed else 0


def datetime_stamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d-%H%M")


def ci_analyze(settings, text: str, archive: str, daily_limit: int, reply_to: str = "",
               quiet_unknown: bool = False) -> int:
    """GitHub Actions (analyst.yml): one requested analysis, answered in the chat it was
    asked in (the owner's private chat or group; anything else: where the bot sends). The
    log is public: this prints one status line; the details go to the private log."""
    import os

    from tascreen import notify
    from tascreen.analyst.request import company_name, handle_request
    from tascreen.store import Store

    bot = notify.from_environment()
    if bot is None:
        print("analysis: telegram is not configured")
        return 1
    known = {(os.environ.get(name) or "").strip() for name in ("TELEGRAM_CHAT_ID", "TELEGRAM_GROUP_ID")} - {""}
    if reply_to.strip() in known:
        bot.chat_id = reply_to.strip()
    store = Store(settings.data_dir)
    try:
        status = handle_request(text, bars_dir=store.bars_dir, read_bars=store.read_bars,
                                archive=Path(archive), bot=bot, make_llm=lambda: _analyst_llm(settings),
                                daily_limit=daily_limit, quiet_unknown=quiet_unknown,
                                name_of=lambda s: company_name(store.scans_dir, s, store.universe_dir))
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


def setup_x(settings) -> int:
    import getpass
    import time

    from tascreen import xnews

    print("\nחיבור השירות שקורא ציוצים מ-X. הדבק את המפתח מהאתר twitterapi.io ולחץ Enter.")
    print("המפתח לא יוצג על המסך בזמן ההדבקה, וזה תקין.\n")
    key = getpass.getpass("Key: ").strip()
    if not key:
        print("\nלא הודבק מפתח.\n")
        return 1
    try:
        xnews.XSource(key).search_page(f"from:Reuters since_time:{int(time.time()) - 3600}")
    except ScreenerError as exc:
        print(f"\nהמפתח לא עובד: {exc}\n")
        return 1
    path = xnews.save_key(key)
    print(f"\nהמפתח עובד ונשמר במחשב: {path}")
    print("עכשיו שמור אותו גם ב-GitHub (Settings > Secrets and variables > Actions):")
    print("  X_API_KEY = אותו מפתח שהדבקת עכשיו\n")
    return 0


def _x_source():
    from tascreen import xnews

    key = xnews.key_from_environment()
    if not key:
        raise ConfigError("no twitterapi.io key: run `run.py --setup-x` (or set X_API_KEY)")
    return xnews.XSource(key)


def x_discover(settings, account: str) -> int:
    """The real answer's shape, so the fields in tascreen/xnews.py are confirmed, not guessed."""
    import time

    from tascreen import xnews

    name = xnews.account_name(account)
    page = _x_source().search_page(f"from:{name} since_time:{int(time.time()) - 7 * 86400}")
    print(f"answer keys: {sorted(page)}")
    tweets = page.get("tweets")
    print(f"tweets: {type(tweets).__name__}, {len(tweets) if isinstance(tweets, list) else '-'} on this page")
    if isinstance(tweets, list) and tweets and isinstance(tweets[0], dict):
        first = tweets[0]
        print(f"post keys: {sorted(first)}")
        if isinstance(first.get("author"), dict):
            print(f"author keys: {sorted(first['author'])}")
        post = xnews.to_post(first)
        print(f"parsed: id={post.id} author={post.author} reply={post.is_reply} "
              f"created={post.created_at} text={len(post.text)} chars")
    return 0


def x_latest(settings, minutes: int) -> int:
    import os
    import time

    from tascreen import xnews

    if os.environ.get("GITHUB_ACTIONS"):
        raise ConfigError("--x-latest prints post texts: not in a public Actions log")
    minutes = max(5, min(int(minutes), 360))
    posts = _x_source().new_posts(list(settings.xnews.accounts), int(time.time()) - minutes * 60)
    for post in posts:
        print(f"[{post.created_at}] @{post.author}: {post.text}\n  {post.url}"
              + (f"\n  picture: {post.photo}" if post.photo else ""))
    print(f"\n{len(posts)} posts in the last {minutes} minutes")
    return 0


def x_vision_check(settings) -> int:
    """The picture call on a real post, end to end. The public log gets one status word;
    the explanation (or the error) goes to the owner's Telegram."""
    import time

    from tascreen import xnews
    from tascreen.llm import ClaudeCodeLLM
    from tascreen.notify import from_environment

    cfg, bot = settings.xnews, from_environment()
    if bot is None:
        raise ConfigError("no Telegram bot: run `run.py --setup-telegram`")
    source = _x_source()
    post = None
    for account in ("charliebilello", "KobeissiLetter", "Callum_Thomas"):
        page = source.search_page(f"from:{account} since_time:{int(time.time()) - 7 * 86400} filter:images")
        post = next((p for p in map(xnews.to_post, page.get("tweets") or []) if p.photo), None)
        if post:
            break
    if post is None:
        print("vision: no post with a picture found")
        return 1
    pick = {"importance": 4, "summary_he": "בדיקה: כך ייראה הסבר לתמונה מציוץ אמיתי."}
    try:
        llm = ClaudeCodeLLM(model=cfg.model, effort=cfg.effort, timeout_s=cfg.timeout_s)
        explained = xnews.explain_images([(post, pick)], llm)
    except ScreenerError as exc:
        bot.send(f"🧪 בדיקת ההסבר לתמונות נכשלה: {exc}"[:3500])
        print(f"vision: failed ({type(exc).__name__})")
        return 1
    bot.send_photo_url(post.photo, "🧪 " + xnews.item(post, pick, with_image=True), html=True)
    print(f"vision: ok (explained={explained})")
    return 0


def xnews_pass(settings) -> int:
    from datetime import datetime, timezone

    from tascreen import xnews
    from tascreen.llm import ClaudeCodeLLM
    from tascreen.notify import from_environment

    cfg = settings.xnews
    if not cfg.enabled:
        print("xnews: disabled in config.yaml")
        return 0
    bot = from_environment()
    if bot is None:
        raise ConfigError("no Telegram bot: run `run.py --setup-telegram` (or set TELEGRAM_BOT_TOKEN/_CHAT_ID)")
    now, state_path = datetime.now(timezone.utc), settings.data_dir / "xnews" / "state.json"
    if xnews.past_end(until=cfg.until, now=now, state_path=state_path, send=bot.send):
        print(f"xnews: status=ended (after {cfg.until})")
        return 0
    summary = xnews.run_once(
        accounts=list(cfg.accounts), source=_x_source(),
        llm_factory=lambda: ClaudeCodeLLM(model=cfg.model, effort=cfg.effort, timeout_s=cfg.timeout_s),
        send=lambda text: bot.send(text, html=True),
        send_photo=lambda url, caption: bot.send_photo_url(url, caption, html=True),
        state_path=state_path, now=now,
        min_importance=cfg.min_importance, daily_read_cap=cfg.daily_read_cap,
        weekend_min_importance=cfg.weekend_min_importance,
        llm_daily_cap=cfg.claude_daily_cap, llm_min_interval_s=cfg.claude_every_minutes * 60,
        max_per_round=cfg.max_per_round, daily_max=cfg.daily_max, low_balance_usd=cfg.low_balance_usd,
        holdings=_saved_holdings(settings), send_private=_private_sender(),
        with_replies=cfg.with_replies)
    print("xnews: " + " ".join(f"{k}={v}" for k, v in summary.items()))
    return 0


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
        (args.backtest, lambda: backtest(settings)),
        (args.backfill_outcomes, lambda: backfill_outcomes(settings, args.limit)),
        (args.backfill_needed, lambda: backfill_needed(settings)),
        (args.ci_probe, lambda: ci_probe(settings, args.limit)),
        (args.ci_tick, lambda: ci_tick(settings, args.limit, args.max_minutes)),
        (args.export_webhook, lambda: export_webhook(args.export_webhook)),
        (args.setup_telegram, lambda: setup_telegram(settings)),
        (args.ci_notify, lambda: ci_notify(settings, args.ci_notify)),
        (args.analyze, lambda: analyze(settings, args.analyze, args.out, with_llm=not args.no_llm,
                                       to_telegram=args.telegram)),
        (args.ci_premarket, lambda: ci_premarket(settings)),
        (args.ensure_live, lambda: ensure_live(settings)),
        (args.digest, lambda: digest(settings)),
        (args.digest_now, lambda: digest(settings, force=True)),
        (args.digest_trial, lambda: digest(settings, trial=True)),
        (args.research_dry, lambda: research_dry(settings)),
        (args.ci_analyze, lambda: ci_analyze(settings, args.ci_analyze, args.archive, args.daily_limit,
                                             args.reply_to, args.quiet_unknown)),
        (args.ci_live, lambda: ci_live(settings, args.max_minutes or 345)),
        (args.telegram_webhook, lambda: telegram_webhook(settings, args.telegram_webhook)),
        (args.analyze_eval is not None, lambda: analyze_eval(settings, args.analyze_eval or None, args.until)),
        (args.setup_x, lambda: setup_x(settings)),
        (args.x_discover, lambda: x_discover(settings, args.x_discover)),
        (args.x_vision_check, lambda: x_vision_check(settings)),
        (args.x_latest is not None, lambda: x_latest(settings, args.x_latest)),
        (args.xnews, lambda: xnews_pass(settings)),
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
