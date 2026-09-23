"""--discover against an in-process stand-in for TradingView's server.
All values are synthetic; only the tool names and argument names are real."""
from __future__ import annotations

import json

from tascreen.discover import PROBES, Probe, describe_shape, run_probes
from tascreen.tv.mcp_client import TradingViewMCP, is_read_only


def _fake_server():
    from mcp.server import MCPServer

    srv = MCPServer("fake-tradingview")

    @srv.tool(name="mcp-tv-get-screener-columns")
    def columns(market: str = "all", group: str = "", search: str = "") -> dict:
        return {"success": True, "columns": [{"name": "synthetic_col", "group": group or "misc"}]}

    @srv.tool(name="mcp-tv-run-screener")
    def screener(market: str = "america", filters: dict | None = None, sort_by: str = "volume",
                 sort_order: str = "desc", limit: int = 100,
                 symbol_types: list[str] | None = None) -> dict:
        return {"success": False,
                "error": "tradingview api: https://scanner.tradingview.com/america/scan: 429: "}

    @srv.tool(name="mcp-tv-get-ohlcv")
    def ohlcv(symbol: str, interval: str = "1D", count: int = 300) -> dict:
        return {"success": False, "error": f"unknown symbol {symbol}"}

    return srv


def test_every_probe_is_read_only():
    assert all(is_read_only(p.tool) for p in PROBES)


def test_probes_record_answers_rate_limits_and_failures_without_stopping(tmp_path):
    client = TradingViewMCP(server=_fake_server())
    probes = (
        Probe("cols", "mcp-tv-get-screener-columns", {"search": "candle"}, "w"),
        Probe("scan", "mcp-tv-run-screener", {"limit": 1}, "w"),
        Probe("bars", "mcp-tv-get-ohlcv", {"symbol": "NASDAQ:ZZZ"}, "w"),
    )
    report = run_probes(client, tmp_path, delays=(), probes=probes)

    assert [e["status"] for e in report] == ["ok", "rate_limited", "failed"]
    saved = json.loads((tmp_path / "cols.json").read_text(encoding="utf-8"))
    assert saved["columns"][0]["name"] == "synthetic_col"
    assert any("columns: list (1 items)" in line for line in report[0]["shape"])
    assert "unknown symbol" in report[2]["error"]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert [e["name"] for e in summary] == ["cols", "scan", "bars"]


def test_shape_shows_structure_not_values():
    lines = describe_shape({"data": {"rows": [{"s": "NASDAQ:X", "v": 1.5}], "totalCount": 7}})
    text = "\n".join(lines)
    assert "rows: list (1 items)" in text and "totalCount: int" in text
    assert "NASDAQ:X" not in text and "1.5" not in text
