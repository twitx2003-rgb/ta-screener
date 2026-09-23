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
