"""The bot's agent roles: one Markdown file per role (owner, 2026-09-28: dedicated teams,
each role defined in one place).

A file opens with a small front matter (name, team, what it does, where it is used)
and then the role's instructions, which are the model's system prompt. Two teams:

- news desk (tascreen/xnews.py, tascreen/explain.py): news-screener (with
  news-deduper), chart-reader, market-explainer;
- breakout research (tascreen/research.py): pattern-auditor, context-analyst,
  statistician, chief-strategist, learning-coach.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..errors import ConfigError

AGENTS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def card(name: str) -> dict[str, str]:
    """The role's front matter as a dict, and its instructions under "prompt"."""
    path = AGENTS_DIR / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise ConfigError(f"no agent role file {path.name}") from None
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ConfigError(f"{path.name}: must open with a --- front matter block")
    head, body = text[4:].split("\n---\n", 1)
    fields = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    if fields.get("name") != name:
        raise ConfigError(f"{path.name}: name must be {name!r}")
    return {**fields, "prompt": body.strip()}


def prompt(*names: str) -> str:
    """The system prompt of one role, or of several joined (a role that does two jobs)."""
    return "\n\n".join(card(n)["prompt"] for n in names)
