"""The agent role files (tascreen/agents/*.md): each loads, and its front matter says who
it is, which team it belongs to and where it is used."""
from __future__ import annotations

import pytest

from tascreen import agents
from tascreen.errors import ConfigError

TEAMS = {"news-desk", "breakout-research"}


def test_every_role_file_is_complete():
    names = sorted(p.stem for p in agents.AGENTS_DIR.glob("*.md"))
    assert {"news-screener", "chart-reader"} <= set(names)
    for name in names:
        card = agents.card(name)
        assert card["team"] in TEAMS and card["does"] and card["used_by"], name
        assert len(card["prompt"]) > 200, name


def test_a_missing_or_misnamed_role_fails_loudly(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="no agent role file"):
        agents.card("no-such-role")
    (tmp_path / "x.md").write_text("---\nname: y\n---\ntext", encoding="utf-8")
    monkeypatch.setattr(agents, "AGENTS_DIR", tmp_path)
    agents.card.cache_clear()
    with pytest.raises(ConfigError, match="name must be 'x'"):
        agents.card("x")
    agents.card.cache_clear()
