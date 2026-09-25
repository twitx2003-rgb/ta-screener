"""The analyst's evaluation set (owner, 2026-09-25: "build a team of agents that trains
the analyst"): a fixed set of stocks in different situations, analysed with the full
pipeline into logs/eval/<round>/, for review agents to grade against
knowledge/review.md. Comparing rounds shows whether a change to the knowledge, the
prompt, the engine or the chart made the analyses better. Nothing is sent anywhere.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..llm import LLM
from ..store import Store
from .request import company_name, produce

# one or two stocks per situation (chosen from the 2026-09-23 scan)
EVAL_SET = {
    "NASDAQ:NVDA": "between support and resistance, a large stock",
    "NASDAQ:AAPL": "uptrend, near the 52-week high",
    "NASDAQ:AMD": "uptrend, near the 52-week high",
    "NASDAQ:NFLX": "downtrend",
    "NYSE:HD": "downtrend, near the 52-week low",
    "NASDAQ:TMUS": "near the 52-week low",
    "NYSE:PG": "a range",
    "NYSE:KO": "a dividend stock, mild moves",
    "NASDAQ:TMC": "volatile, a small price",
    "NASDAQ:PLTR": "a fresh breakout (cup with handle)",
    "NASDAQ:AVT": "a fresh breakout (ascending triangle)",
    "NYSE:FIS": "a pattern that failed after its breakout",
}


def run_eval(store: Store, llm: LLM, folder: Path, *, to_png: Callable[[Path, Path], Path] | None = None,
             progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Analyse every stock of the set into `folder` (one subfolder each) and write
    index.json: what was analysed, the situation it stands for, and what went wrong."""
    folder.mkdir(parents=True, exist_ok=True)
    index: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "model": getattr(llm, "name", "?"), "stocks": []}
    for n, (symbol, situation) in enumerate(EVAL_SET.items(), 1):
        progress(f"  {n}/{len(EVAL_SET)} {symbol} ({situation})...")
        entry: dict[str, Any] = {"symbol": symbol, "situation": situation}
        bars = store.read_bars(symbol)
        if bars is None:
            entry["error"] = "no stored bars"
        else:
            out = folder / symbol.replace(":", "_")
            try:
                done = produce(symbol, bars, out, llm=llm, name=company_name(store.scans_dir, symbol))
                entry.update(folder=out.name, stem=done["stem"],
                             omitted=done["written"]["omitted"] if done["written"] else None)
                if to_png is not None:
                    to_png(out / f"{done['stem']}.svg", out / f"{done['stem']}.png")
            except Exception as exc:                  # one stock's failure is a finding, not a stop
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        index["stocks"].append(entry)
    (folder / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return index
