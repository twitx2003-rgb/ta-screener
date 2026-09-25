"""A requested analysis on the runner: limit, symbol, files, Telegram (made-up prices)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from synth import from_knots
from tascreen.analyst.request import handle_request, shown
from tascreen.errors import ProviderError
from tascreen.llm import SyntheticLLM
from tascreen.notify import Telegram

KNOTS = [(0, 60)] + [(k, 60 + 0.12 * k + (8 if (k // 20) % 2 else -8)) for k in range(20, 400, 20)]
NOW = datetime(2026, 3, 20, 21, 5, 7, tzinfo=timezone.utc)


def _answer(system, user, schema):
    import json

    facts = json.loads(user.split("BRIEF (JSON):\n", 1)[1].split("\n\nYour previous", 1)[0])["facts"]
    cites = {"headline": ["close"],
             "levels": [k for k in facts if k.startswith("level.")][:1] or ["close"],
             "up": ["up.trigger"] if "up.trigger" in facts else ["close"],
             "down": ["down.trigger"] if "down.trigger" in facts else ["close"]}
    return {"parts": [{"part": p, "signal": "yellow", "text": "בדיקה.", "cites": cites[p]}
                      for p in ("headline", "levels", "up", "down")]}


class _Bot(Telegram):
    def __init__(self):
        self.sent = []
        super().__init__("123456:" + "x" * 30, 42,
                         post=lambda m, p: self.sent.append((m, p.get("text"))) or {"ok": True},
                         upload=lambda m, p, f: self.sent.append((m, f[1])) or {"ok": True})


def _png(svg, png):
    png.write_bytes(b"PNG")
    return png


def _request(tmp_path, text, *, limit=10, llm=None):
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir(exist_ok=True)
    (bars_dir / "NYSE_SYN.parquet").write_bytes(b"")
    bot = _Bot()
    llm = llm or SyntheticLLM(_answer)
    status = handle_request(text, bars_dir=bars_dir, read_bars=lambda s: from_knots(KNOTS),
                            archive=tmp_path / "analyses", bot=bot, make_llm=lambda: llm,
                            daily_limit=limit, to_png=_png, now=lambda: NOW)
    return status, bot, llm


def test_a_request_sends_chart_text_and_script_and_keeps_the_files(tmp_path):
    status, bot, llm = _request(tmp_path, "syn")
    assert status == "sent NYSE:SYN" and len(llm.calls) == 1
    assert [m for m, _ in bot.sent] == ["sendPhoto", "sendMessage", "sendDocument"]
    folder = tmp_path / "analyses" / "2026-03-20" / "210507-NYSE_SYN"
    kept = sorted(p.suffix for p in folder.iterdir())
    assert kept == [".html", ".json", ".json", ".pine", ".png", ".svg"]


def test_the_daily_limit_answers_without_calling_the_model(tmp_path):
    for n in range(2):
        (tmp_path / "analyses" / "2026-03-20" / f"10000{n}-NYSE_X").mkdir(parents=True)
    status, bot, llm = _request(tmp_path, "syn", limit=2)
    assert status == "daily limit" and not llm.calls
    assert bot.sent[0][0] == "sendMessage" and "2 ניתוחים ביום" in bot.sent[0][1]


def test_an_unknown_symbol_is_answered_with_only_symbol_characters(tmp_path):
    status, bot, llm = _request(tmp_path, "<b>zz</b> & rm")
    assert status == "not in the list" and not llm.calls
    assert bot.sent[0][1].startswith("bzzbrm: המניה לא ברשימה")
    assert shown("") == "?" and shown("nasdaq:nvda") == "nasdaq:nvda"


def test_a_failure_is_told_and_raised(tmp_path):
    def broken(system, user, schema):
        raise ProviderError("Claude Code did not answer")
    with pytest.raises(ProviderError):
        _request(tmp_path, "NYSE:SYN", llm=SyntheticLLM(broken))
    # the folder counts toward the daily limit, so a failing request cannot loop on the quota
    assert (tmp_path / "analyses" / "2026-03-20" / "210507-NYSE_SYN").is_dir()
