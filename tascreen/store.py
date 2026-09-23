"""Where fetched data lives: data/ (gitignored — TradingView data is not redistributed).

    data/universe/<YYYY-MM-DD>.parquet   universe snapshot (contract UNIVERSE)
    data/universe/<YYYY-MM-DD>.json      how it was fetched (bands, counts)
    data/bars/<EXCHANGE_TICKER>.parquet  daily bars per symbol (contract BARS)
    data/bars/status.json                last outcome per symbol
    data/scans/<YYYY-MM-DD>/             one scan per last completed session:
        indicators.parquet (INDICATORS), patterns.parquet (PATTERNS), scan.json
    data/quotes/latest.parquet           the newest live quotes (QUOTES), latest.json
    data/quotes/live_state.json          what the live loop last did (the daily update)
    data/channels/<YYYY-MM-DD>/<channel>.json   agent threads (daily; live.json for crossings)
    data/channels/<YYYY-MM-DD>/charts/<post>.svg chart screenshots with drawings
    data/outcomes/ledger.parquet         every breakout and its outcome (OUTCOMES), meta.json

Writes go to a temporary file first and replace the target, so an interrupted
run never leaves half a file behind.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .contracts import (BARS, INDICATORS, OUTCOMES, PATTERNS, QUOTES, UNIVERSE,
                        canonical_timestamps)


def _atomic_write_bytes(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    write(tmp)
    os.replace(tmp, path)


def _write_json(path: Path, data: Any) -> None:
    _atomic_write_bytes(path, lambda tmp: tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8"))


def symbol_file_stem(symbol: str) -> str:
    """NASDAQ:NVDA -> NASDAQ_NVDA (':' is not allowed in Windows file names)."""
    return re.sub(r"[^A-Za-z0-9.\-]", "_", symbol)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.universe_dir = self.root / "universe"
        self.bars_dir = self.root / "bars"
        self.scans_dir = self.root / "scans"
        self.quotes_dir = self.root / "quotes"
        self.channels_dir = self.root / "channels"
        self.outcomes_dir = self.root / "outcomes"

    # ------------------------------------------------------------- outcomes
    def read_ledger(self) -> pd.DataFrame | None:
        path = self.outcomes_dir / "ledger.parquet"
        return OUTCOMES.validate(pd.read_parquet(path)) if path.exists() else None

    def write_ledger(self, frame: pd.DataFrame, meta: dict[str, Any]) -> None:
        OUTCOMES.validate(frame)
        _atomic_write_bytes(self.outcomes_dir / "ledger.parquet",
                            lambda tmp: frame.to_parquet(tmp, index=False))
        _write_json(self.outcomes_dir / "meta.json", meta)          # last: marks it complete

    def read_outcomes_meta(self) -> dict[str, Any]:
        path = self.outcomes_dir / "meta.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    # ------------------------------------------------------------- channels
    def write_channel_doc(self, day: date, name: str, doc: dict[str, Any]) -> None:
        _write_json(self.channels_dir / day.isoformat() / f"{name}.json", doc)

    def read_channel_doc(self, day: date, name: str) -> dict[str, Any] | None:
        path = self.channels_dir / day.isoformat() / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def channel_days(self) -> list[date]:
        if not self.channels_dir.exists():
            return []
        days = []
        for folder in self.channels_dir.iterdir():
            try:
                days.append(date.fromisoformat(folder.name))
            except ValueError:
                continue
        return sorted(days)

    def write_channel_chart(self, day: date, post_id: str, svg: str) -> str:
        name = f"{symbol_file_stem(post_id)}.svg"
        _atomic_write_bytes(self.channels_dir / day.isoformat() / "charts" / name,
                            lambda tmp: tmp.write_text(svg, encoding="utf-8"))
        return name

    def read_channel_chart(self, day: date, name: str) -> str | None:
        path = self.channels_dir / day.isoformat() / "charts" / symbol_file_stem(Path(name).stem)
        path = path.with_suffix(".svg")
        return path.read_text(encoding="utf-8") if path.exists() else None

    # ------------------------------------------------------------- universe
    def write_universe(self, day: date, frame: pd.DataFrame, summary: dict[str, Any]) -> Path:
        UNIVERSE.validate(frame)
        path = self.universe_dir / f"{day.isoformat()}.parquet"
        _atomic_write_bytes(path, lambda tmp: frame.to_parquet(tmp, index=False))
        _write_json(path.with_suffix(".json"), summary)
        return path

    def universe_days(self) -> list[date]:
        if not self.universe_dir.exists():
            return []
        days = []
        for path in self.universe_dir.glob("*.parquet"):
            try:
                days.append(date.fromisoformat(path.stem))
            except ValueError:
                continue
        return sorted(days)

    def latest_universe(self) -> tuple[date, pd.DataFrame] | None:
        days = self.universe_days()
        if not days:
            return None
        day = days[-1]
        frame = pd.read_parquet(self.universe_dir / f"{day.isoformat()}.parquet")
        return day, UNIVERSE.validate(frame)

    # ----------------------------------------------------------------- bars
    def bars_path(self, symbol: str) -> Path:
        return self.bars_dir / f"{symbol_file_stem(symbol)}.parquet"

    def read_bars(self, symbol: str) -> pd.DataFrame | None:
        path = self.bars_path(symbol)
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        frame["timestamp"] = canonical_timestamps(frame["timestamp"])
        return BARS.validate(frame)

    def write_bars(self, symbol: str, frame: pd.DataFrame) -> None:
        BARS.validate(frame)
        _atomic_write_bytes(self.bars_path(symbol), lambda tmp: frame.to_parquet(tmp, index=False))

    def read_status(self) -> dict[str, dict[str, Any]]:
        path = self.bars_dir / "status.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def write_status(self, status: dict[str, dict[str, Any]]) -> None:
        _write_json(self.bars_dir / "status.json", status)

    # ---------------------------------------------------------------- scans
    def write_scan(self, day: date, indicators: pd.DataFrame, patterns: pd.DataFrame,
                   summary: dict[str, Any]) -> Path:
        INDICATORS.validate(indicators)
        PATTERNS.validate(patterns)
        folder = self.scans_dir / day.isoformat()
        _atomic_write_bytes(folder / "indicators.parquet",
                            lambda tmp: indicators.to_parquet(tmp, index=False))
        _atomic_write_bytes(folder / "patterns.parquet",
                            lambda tmp: patterns.to_parquet(tmp, index=False))
        _write_json(folder / "scan.json", summary)       # last: marks the scan complete
        return folder

    def scan_days(self) -> list[date]:
        if not self.scans_dir.exists():
            return []
        days = []
        for folder in self.scans_dir.iterdir():
            try:
                day = date.fromisoformat(folder.name)
            except ValueError:
                continue
            if (folder / "scan.json").exists():
                days.append(day)
        return sorted(days)

    def read_scan(self, day: date) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        folder = self.scans_dir / day.isoformat()
        indicators = INDICATORS.validate(pd.read_parquet(folder / "indicators.parquet"))
        patterns = PATTERNS.validate(pd.read_parquet(folder / "patterns.parquet"))
        summary = json.loads((folder / "scan.json").read_text(encoding="utf-8"))
        return indicators, patterns, summary

    # --------------------------------------------------------------- quotes
    def write_quotes(self, frame: pd.DataFrame, summary: dict[str, Any]) -> None:
        QUOTES.validate(frame)
        _atomic_write_bytes(self.quotes_dir / "latest.parquet",
                            lambda tmp: frame.to_parquet(tmp, index=False))
        _write_json(self.quotes_dir / "latest.json", summary)     # last: marks them complete

    def read_quotes(self) -> tuple[pd.DataFrame, dict[str, Any]] | None:
        meta = self.quotes_dir / "latest.json"
        if not meta.exists():
            return None
        frame = QUOTES.validate(pd.read_parquet(self.quotes_dir / "latest.parquet"))
        return frame, json.loads(meta.read_text(encoding="utf-8"))

    def read_live_state(self) -> dict[str, Any]:
        path = self.quotes_dir / "live_state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def write_live_state(self, state: dict[str, Any]) -> None:
        _write_json(self.quotes_dir / "live_state.json", state)
