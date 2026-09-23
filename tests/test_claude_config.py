"""The Claude Code permissions must agree with the Python client's read-only rule."""
from __future__ import annotations

import json

from tascreen.config import ROOT
from tascreen.tv.mcp_client import is_read_only

PREFIX = "mcp__tradingview__"

# Every tool TradingView's server listed that changes the account (35 tools listed
# live on 2026-09-22; these 10 are the ones is_read_only refuses).
WRITE_TOOLS = {
    "mcp-tv-create-alert", "mcp-tv-update-alert", "mcp-tv-delete-alert",
    "mcp-tv-restart-alerts", "mcp-tv-stop-alerts",
    "mcp-watchlist-create-watchlist", "mcp-watchlist-update-watchlist",
    "mcp-watchlist-add-to-watchlist", "mcp-watchlist-remove-from-watchlist",
    "mcp-watchlist-delete-watchlist",
}


def _permissions():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    return settings["permissions"]


def test_every_write_tool_is_denied_and_refused_by_the_client():
    denied = {rule.removeprefix(PREFIX) for rule in _permissions()["deny"]}
    assert denied == WRITE_TOOLS
    assert not any(is_read_only(name) for name in WRITE_TOOLS)


def test_only_read_tools_are_pre_allowed():
    allowed = [rule.removeprefix(PREFIX) for rule in _permissions()["allow"]
               if rule.startswith(PREFIX)]
    assert allowed and all(is_read_only(name) for name in allowed)


def test_mcp_server_config_holds_no_secrets():
    text = (ROOT / ".mcp.json").read_text(encoding="utf-8")
    config = json.loads(text)["mcpServers"]["tradingview"]
    assert config == {"type": "http", "url": "https://mcp.tradingview.com/mcp"}
