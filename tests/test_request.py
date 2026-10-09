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
    cites = {"headline": ["event"] if "event" in facts else ["close"],
             "levels": [k for k in facts if k.startswith("level.")][:1] or ["close"],
             "up": ["up.trigger"] if "up.trigger" in facts else ["close"],
             "down": ["down.trigger"] if "down.trigger" in facts else ["close"]}
    return {"parts": [{"part": p, "signal": "yellow", "text": "בדיקה.", "cites": cites[p]}
                      for p in ("headline", "levels", "up", "down")]}


class _Bot(Telegram):
    def __init__(self):
        self.sent, self.captions = [], []
        super().__init__("123456:" + "x" * 30, 42,
                         post=lambda m, p: self.sent.append((m, p.get("text"))) or {"ok": True},
                         upload=lambda m, p, f: self.sent.append((m, f[1])) or
                         self.captions.append(p.get("caption")) or {"ok": True})


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


def test_a_word_written_alone_in_the_group_gets_no_answer(tmp_path):
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    bot = _Bot()
    status = handle_request("LOL", bars_dir=bars_dir, read_bars=lambda s: None, archive=tmp_path / "a",
                            bot=bot, make_llm=lambda: pytest.fail("no model"), quiet_unknown=True,
                            now=lambda: NOW)
    assert status == "not in the list (no answer)" and bot.sent == []


def test_the_png_waits_for_the_tools_installed_in_the_background(tmp_path, monkeypatch):
    from tascreen.analyst.png import wait_for_tools

    flag = tmp_path / "rsvg.ready"
    monkeypatch.setenv("RSVG_READY_FLAG", str(flag))
    ticks = iter(range(1000))
    naps = []
    assert not wait_for_tools(3, clock=lambda: next(ticks), sleep=naps.append)       # never came
    flag.touch()
    assert wait_for_tools(3, clock=lambda: 0, sleep=naps.append) and len(naps) == 2      # at once
    monkeypatch.delenv("RSVG_READY_FLAG")
    assert wait_for_tools(0)                                                       # not on a runner


def test_a_request_sends_one_chart_with_a_short_line_and_keeps_the_files(tmp_path):
    status, bot, llm = _request(tmp_path, "syn")
    assert status == "sent NYSE:SYN" and len(llm.calls) == 1
    assert [m for m, _ in bot.sent] == ["sendPhoto"]            # no long text, no Pine Script
    caption = bot.captions[0].splitlines()
    assert len(caption) == 3 and "SYN" in caption[0]                # name, the line, the footer
    assert "בדיקה." in caption[1] and caption[2].lstrip("‏").startswith("<i>")
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
    assert bot.sent[0][1].startswith("‏bzzbrm: המניה לא ברשימה")  # right-aligned
    assert shown("") == "?" and shown("nasdaq:nvda") == "nasdaq:nvda"


def test_a_failure_is_told_and_raised(tmp_path):
    def broken(system, user, schema):
        raise ProviderError("Claude Code did not answer")
    with pytest.raises(ProviderError):
        _request(tmp_path, "NYSE:SYN", llm=SyntheticLLM(broken))
    # the folder counts toward the daily limit, so a failing request cannot loop on the quota
    assert (tmp_path / "analyses" / "2026-03-20" / "210507-NYSE_SYN").is_dir()


def test_the_evaluation_set_writes_every_stock_and_an_index(tmp_path, monkeypatch):
    import json

    from tascreen.analyst import evaluate

    monkeypatch.setattr(evaluate, "EVAL_SET", {"NYSE:SYN": "a test", "NYSE:NONE": "no bars"})

    class Store:
        scans_dir = tmp_path / "scans"

        def read_bars(self, symbol):
            return from_knots(KNOTS) if symbol == "NYSE:SYN" else None

    index = evaluate.run_eval(Store(), SyntheticLLM(_answer), tmp_path / "round1",
                              to_png=_png, progress=lambda m: None)
    by = {s["symbol"]: s for s in index["stocks"]}
    assert by["NYSE:NONE"]["error"] == "no stored bars" and by["NYSE:SYN"]["omitted"] == []
    kept = sorted(p.suffix for p in (tmp_path / "round1" / "NYSE_SYN").iterdir())
    assert kept == [".html", ".json", ".json", ".pine", ".png", ".svg"]
    assert json.loads((tmp_path / "round1" / "index.json").read_text(encoding="utf-8"))["stocks"]
    # a later round reads the same days as an earlier one
    early = evaluate.run_eval(Store(), SyntheticLLM(_answer), tmp_path / "round2", to_png=_png,
                              progress=lambda m: None, until="1990-01-01")
    assert early["until"] == "1990-01-01"
    assert {s["symbol"]: s.get("error") for s in early["stocks"]}["NYSE:SYN"] == "no stored bars up to 1990-01-01"


def test_a_failed_analysis_says_sorry_without_the_exception(tmp_path):
    def broken(system, user, schema):
        raise KeyError("made-up")

    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    (bars_dir / "NYSE_SYN.parquet").write_bytes(b"")
    bot = _Bot()
    with pytest.raises(KeyError):
        handle_request("syn", bars_dir=bars_dir, read_bars=lambda s: from_knots(KNOTS),
                       archive=tmp_path / "analyses", bot=bot, make_llm=lambda: SyntheticLLM(broken),
                       to_png=_png, now=lambda: NOW)
    told = bot.sent[-1][1]
    assert "סליחה" in told and "NYSE:SYN" in told and "KeyError" not in told and "made-up" not in told


def test_company_name_falls_back_to_the_universe(tmp_path):
    import pandas as pd
    from tascreen.analyst.request import company_name

    scans, universe = tmp_path / "scans", tmp_path / "universe"
    (scans / "2026-01-05").mkdir(parents=True)
    universe.mkdir()
    pd.DataFrame({"symbol": ["NASDAQ:BIGA"], "description": ["Big A Corp"]}).to_parquet(
        scans / "2026-01-05" / "indicators.parquet")
    pd.DataFrame({"symbol": ["NASDAQ:SMLB"], "description": ["Small B Inc"]}).to_parquet(
        universe / "2026-01-05.parquet")
    assert company_name(scans, "NASDAQ:BIGA", universe) == "Big A Corp"
    assert company_name(scans, "NASDAQ:SMLB", universe) == "Small B Inc"
    assert company_name(scans, "NASDAQ:SMLB") is None
    assert company_name(scans, "NASDAQ:NONE", universe) is None
