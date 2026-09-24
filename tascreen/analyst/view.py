"""The simple view: what the chart and the Pine Script show by default (owner, 2026-09-24:
"simple and to the point - support and resistance, Fibonacci and the volume profile when
needed"). The facts keep everything else (trendlines, divergences, patterns, averages)
for the written analysis.

- the nearest support and resistance zones, `view_zones_each_side` on each side;
- the Fibonacci retracements (38.2 / 50 / 61.8 only) while the price is inside a pullback
  of the last big move;
- the volume profile when its POC is near the price.
A Fibonacci level or the POC that falls on a drawn zone is not drawn again: it joins the
zone's label as a note, so one level never carries two labels.
"""
from __future__ import annotations

import math
from typing import Any

from . import load_rules
from .facts import Analysis


def note_parts(key: str) -> tuple[str, str]:
    """A zone note's word and number, kept apart so each renderer can order them."""
    if key == "poc":
        return "POC", ""
    return "פיבו", f"{int(key.rsplit('_', 1)[1]) / 10:g}%"


def simple_view(analysis: Analysis, rules: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """{id: drawing} for the simple picture. Zones carry `notes` (keys: "fib_500", "poc");
    "fib" and "vp", when the analysis has them, carry `show` (whether they matter now):
    the Pine Script offers them as switches, the chart draws only the shown ones."""
    rules = rules or load_rules()
    drawings = analysis.drawings
    close = float(analysis.facts["close"]["value"])
    atr = float((analysis.facts.get("atr") or {}).get("value") or math.nan)
    near = rules["view_confluence_atr"] * atr if math.isfinite(atr) else 0.0
    view: dict[str, dict[str, Any]] = {}
    zones = [(key, item) for key, item in drawings.items() if item["type"] == "zone"]
    for kind in ("resistance", "support"):
        mine = sorted((z for z in zones if z[1]["kind"] == kind),
                      key=lambda z: abs((z[1]["low"] + z[1]["high"]) / 2 - close))
        for key, item in mine[:int(rules["view_zones_each_side"])]:
            view[key] = {**item, "notes": []}

    def zone_at(price: float) -> dict[str, Any] | None:
        for item in view.values():
            if item["type"] == "zone" and item["low"] - near <= price <= item["high"] + near:
                return item
        return None

    fib = drawings.get("fib")
    if fib:
        span = fib["end"] - fib["start"]
        retraced = (fib["end"] - close) / span if span else math.nan
        low, high = rules["view_fib_when_retraced"]
        show = low <= retraced <= high
        levels = {}
        for ratio in rules["view_fib_levels"]:
            key = f"fib_{round(ratio * 1000)}"
            if key not in fib["levels"]:
                continue
            price = fib["levels"][key]
            zone = zone_at(price) if show else None
            if zone is not None:
                zone["notes"].append(key)
            else:
                levels[key] = price
        view["fib"] = {**fib, "levels": levels, "show": show}

    vp = drawings.get("vp")
    if vp:
        show = math.isfinite(atr) and abs(vp["poc"] - close) <= rules["view_vp_poc_within_atr"] * atr
        zone = zone_at(vp["poc"]) if show else None
        if zone is not None:
            zone["notes"].append("poc")
        view["vp"] = {**vp, "show": show, "poc_label": zone is None}
    return view
