"""Which channels exist, and which stocks each channel discusses.

The channels are the patterns with the most detections in the newest scan (chart
and candle patterns ranked together), plus #כללי for the day's notable chart
breakouts and a recap of the counts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..patterns.rules import Rules

GENERAL = "general"
STATUS_RANK = {"breakout": 0, "busted": 1, "signal": 2, "forming": 3}


@dataclass(frozen=True)
class Channel:
    id: str            # "general" or a pattern key from rules.yaml
    name: str          # "#ראש-וכתפיים"
    title: str         # "ראש וכתפיים"
    count: int         # detections behind it in the newest scan
    family: str = ""   # chart | candle | "" (general)


def channel_name(title: str) -> str:
    return "#" + "-".join(title.split())


def channels(detections: pd.DataFrame, rules: Rules, count: int) -> list[Channel]:
    """#כללי first, then the `count` most common patterns (ties by key, so the
    order is stable)."""
    known = {**rules.chart, **rules.candle}
    counts = detections["pattern"].value_counts() if len(detections) else pd.Series(dtype=int)
    ranked = sorted(((key, int(n)) for key, n in counts.items() if key in known),
                    key=lambda kn: (-kn[1], kn[0]))[:count]
    out = [Channel(GENERAL, channel_name("כללי"), "כללי", int(len(detections)))]
    out += [Channel(key, channel_name(known[key].name_he), known[key].name_he, n, known[key].family)
            for key, n in ranked]
    return out


def find(channel_list: list[Channel], channel_id: str) -> Channel | None:
    return next((c for c in channel_list if c.id == channel_id), None)


def pick_topics(stocks: pd.DataFrame, detections: pd.DataFrame, channel_id: str,
                n: int) -> list[dict[str, Any]]:
    """Up to `n` detections to discuss, one per stock: fresh breakouts first, then
    busted, candle signals and patterns still forming; within each, the most recent,
    then the largest company and the highest relative volume."""
    if channel_id == GENERAL:
        det = detections.loc[(detections["family"] == "chart") & (detections["status"] == "breakout")]
    else:
        det = detections.loc[detections["pattern"] == channel_id]
    if det.empty:
        return []
    by_symbol = stocks.set_index("symbol")
    det = det.assign(_rank=det["status"].map(STATUS_RANK).fillna(9),
                     _cap=det["symbol"].map(by_symbol["market_cap"]),
                     _rv=det["symbol"].map(by_symbol["rel_volume"]))
    det = det.sort_values(["_rank", "age", "_cap", "_rv"], ascending=[True, True, False, False],
                          na_position="last", kind="stable")
    det = det.drop_duplicates("symbol").head(n)
    return det.drop(columns=["_rank", "_cap", "_rv"]).to_dict("records")
