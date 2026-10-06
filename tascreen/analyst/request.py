"""One analysis end to end, and a request from the owner (stage T4: a GitHub run started
from the phone; stage T5: the Telegram bot starts the same run).

`produce` writes the files of one analysis (facts, chart, Pine Script, and the written
text when a model is given) and sends them to Telegram when a bot is given.
`handle_request` is the runner's side: a daily limit (the analyses share the Claude
subscription with the X news), the typed symbol resolved against the stored bars, the
analysis kept in `archive/<UTC day>/<time>-<stem>/`, and a short Hebrew answer in
Telegram for a refusal or a failure. Nothing here calls TradingView.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ..errors import ConfigError
from ..llm import LLM
from ..notify import Telegram
from ..store import symbol_file_stem
from . import find_symbol, writer
from .chart import render
from .facts import analyse
from .pine import pine_script

DAILY_LIMIT = 10


def _name_in(path: Path, symbol: str) -> str | None:
    try:
        frame = pd.read_parquet(path, columns=["symbol", "description"])
    except (OSError, ValueError, KeyError):
        return None
    names = frame.loc[frame["symbol"] == symbol, "description"]
    value = names.iloc[0] if len(names) else None
    return value if isinstance(value, str) and value.strip() else None


def company_name(scans_dir: Path, symbol: str, universe_dir: Path | None = None) -> str | None:
    """The company's name from the newest scan's indicators, else from the newest universe
    (the scan covers only the larger stocks, the bot analyses down to $1B). None when
    neither has it."""
    days = sorted(p.name for p in scans_dir.glob("????-??-??") if (p / "indicators.parquet").exists())         if scans_dir.exists() else []
    name = _name_in(scans_dir / days[-1] / "indicators.parquet", symbol) if days else None
    if name is None and universe_dir is not None and universe_dir.exists():
        files = sorted(universe_dir.glob("????-??-??.parquet"))
        name = _name_in(files[-1], symbol) if files else None
    return name


def produce(symbol: str, bars: pd.DataFrame, folder: Path, *, llm: LLM | None = None,
            bot: Telegram | None = None, to_png: Callable[[Path, Path], Path] | None = None,
            name: str | None = None, notes: str = "") -> dict[str, Any]:
    analysis = analyse(bars, symbol)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{symbol_file_stem(symbol)}-{analysis.last_day}"
    svg, pine = folder / f"{stem}.svg", folder / f"{stem}.pine"
    (folder / f"{stem}.json").write_text(analysis.as_json(), encoding="utf-8")
    pine.write_text(pine_script(analysis, bars), encoding="utf-8")
    written = message = None
    if llm is not None:
        written = writer.write(analysis, llm, name=name, notes=notes)
        message = writer.telegram_html(written)
        (folder / f"{stem}.written.json").write_text(json.dumps(written, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
        (folder / f"{stem}.telegram.html").write_text(message, encoding="utf-8")
    # drawn after the text, so the chart shows what the text names (review round 2)
    cited = None if written is None else {c for part in written["parts"] for c in part.get("cites", [])}
    svg.write_text(render(bars, analysis, name=name, cited=cited), encoding="utf-8")
    if bot is not None:
        if to_png is None:
            from .png import svg_to_png as to_png
        png = to_png(svg, folder / f"{stem}.png")
        bot.send_photo(png.read_bytes(), writer.photo_caption(analysis), f"{stem}.png")
        if message:
            bot.send(message, html=True)
        # the Pine Script is kept with the analysis, not sent (owner, 2026-09-29: not needed)
    return {"symbol": symbol, "last_day": analysis.last_day, "stem": stem, "folder": folder,
            "analysis": analysis, "written": written}


def shown(text: str) -> str:
    """What the owner typed, safe to echo back: symbol characters only, short."""
    return re.sub(r"[^A-Za-z0-9:.$\-]", "", text)[:20] or "?"


def handle_request(text: str, *, bars_dir: Path, read_bars: Callable[[str], pd.DataFrame | None],
                   archive: Path, bot: Telegram, make_llm: Callable[[], LLM],
                   daily_limit: int = DAILY_LIMIT, to_png: Callable[[Path, Path], Path] | None = None,
                   name_of: Callable[[str], str | None] = lambda symbol: None,
                   now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                   quiet_unknown: bool = False) -> str:
    """Answer one request; returns a short status for the public log. With `quiet_unknown`
    (a capital word written alone in the owner's group) an unknown symbol gets no answer."""
    stamp = now()
    today = archive / stamp.strftime("%Y-%m-%d")
    done = [p for p in today.iterdir() if p.is_dir()] if today.exists() else []
    if len(done) >= daily_limit:
        bot.send(f"הבוט הגיע למכסה היומית: {daily_limit} ניתוחים ביום. אפשר לבקש שוב מחר.")
        return "daily limit"
    try:
        symbol = find_symbol(bars_dir, text)
        bars = read_bars(symbol)
        if bars is None:
            raise ConfigError(f"no stored bars for {symbol}")
    except ConfigError:
        if quiet_unknown:
            return "not in the list (no answer)"
        bot.send(f"{shown(text)}: המניה לא ברשימה. הבוט מנתח מניות אמריקאיות ששוויין מעל מיליארד "
                 "דולר. אפשר לכתוב למשל NVDA או NASDAQ:NVDA.")
        return "not in the list"
    folder = today / f"{stamp:%H%M%S}-{symbol_file_stem(symbol)}"
    try:
        from ..setups_coach import analyst_notes

        produce(symbol, bars, folder, llm=make_llm(), bot=bot, to_png=to_png, name=name_of(symbol),
                notes=analyst_notes(bars_dir.parent))
    except Exception:
        # members see a plain sorry; the exception itself reaches the run's log (re-raised)
        try:
            bot.send(f"סליחה, הניתוח של {symbol} לא הצליח הפעם. נסו שוב מאוחר יותר.")
        except Exception:
            pass
        raise
    return f"sent {symbol}"
