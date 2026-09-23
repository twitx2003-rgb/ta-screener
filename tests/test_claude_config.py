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


def _frontmatter(path):
    import yaml

    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name}: no frontmatter"
    return yaml.safe_load(text.split("---\n")[1])


def test_agents_get_only_read_only_tradingview_tools():
    agents = sorted((ROOT / ".claude" / "agents").glob("*.md"))
    assert agents
    for path in agents:
        meta = _frontmatter(path)
        assert meta["name"] == path.stem and meta["description"]
        tools = [t.strip() for t in str(meta.get("tools", "")).split(",") if t.strip()]
        assert tools, f"{path.name}: list its tools explicitly (no tools = every tool)"
        for tool in tools:
            if tool.startswith(PREFIX):
                assert is_read_only(tool.removeprefix(PREFIX)), f"{path.name}: {tool}"
        assert "Edit" not in tools and "Write" not in tools, f"{path.name} must not edit files"


def test_skills_have_names_and_descriptions():
    for path in sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")):
        meta = _frontmatter(path)
        assert meta["name"] == path.parent.name and len(meta["description"]) > 50
        assert "mcp" not in meta["name"], "a skill named *mcp* shadows /mcp in autocomplete"


def test_mcp_server_config_holds_no_secrets():
    text = (ROOT / ".mcp.json").read_text(encoding="utf-8")
    config = json.loads(text)["mcpServers"]["tradingview"]
    assert config == {"type": "http", "url": "https://mcp.tradingview.com/mcp"}
