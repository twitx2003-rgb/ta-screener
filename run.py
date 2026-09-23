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
                        help="--universe, then --bars (a rate-limited screener falls back to "
                             "the newest saved universe up to universe.max_age_days old)")
    parser.add_argument("--check-bars", action="store_true",
                        help="Check stored bars against the trading calendar: market-wide "
                             "missing days, per-symbol gaps, symbols behind the last session")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="With --bars/--update: only the N largest symbols (a pilot run)")
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


def bars(settings, limit: int | None) -> int:
    from tascreen.bars import BarsJob, update_all
    from tascreen.store import Store

    found = usable_universe(settings)
    if found is None:
        return 1
    day, frame = found
    symbols = frame["symbol"].tolist()[:limit] if limit else frame["symbol"].tolist()
    job = BarsJob(Store(settings.data_dir), settings.bars, settings.market,
                  settings.tradingview.rate_limit_delays)
    print(f"\nBars for {len(symbols)} symbols (universe of {day}); "
          f"last completed session: {job.target}\n")
    report = update_all(make_tradingview(settings), job, symbols)
    (settings.log_dir / "bars_last_run.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDone in {report['seconds']:.0f} s, {report['calls']} calls "
          f"({report['seconds_per_call']} s/call): {report['counts']}")
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
    print(f"  full report: {settings.log_dir / 'bars_audit.json'}\n")
    bad = report["market_wide_missing_days"] or report["bars_on_non_trading_days"]
    return 1 if bad else 0


def update(settings, limit: int | None) -> int:
    from tascreen.tv.data import RateLimited

    try:
        universe(settings)
    except RateLimited as exc:
        log.warning("screener rate limited (%s); trying the newest saved universe", exc)
        if usable_universe(settings, max_age_days=settings.universe.max_age_days) is None:
            return 1
    return bars(settings, limit)


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
