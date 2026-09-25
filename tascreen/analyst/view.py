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


def key_level_facts(analysis: Analysis, rules: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Facts for exactly what the chart shows, and the two scenarios built on it, so the
    written analysis quotes the reader's levels and never picks its own:
    - level.s1 / level.s2 (supports, nearest first), level.r1 / level.r2 (resistances):
      low, high, touches, and the close's distance to the zone's near edge;
    - position: where the close stands, in words;
    - up.trigger (a close above the nearest resistance), up.next (the next resistance),
      up.cancel (a close below the nearest support), and down.* the other way round."""
    from .facts import _Facts

    view = simple_view(analysis, rules)
    close = float(analysis.facts["close"]["value"])
    zones = [v for v in view.values() if v["type"] == "zone"]
    res = sorted((z for z in zones if z["kind"] == "resistance"), key=lambda z: z["low"])
    sup = sorted((z for z in zones if z["kind"] == "support"), key=lambda z: -z["high"])
    f = _Facts()
    for kind, name, found in (("r", "התנגדות", res), ("s", "תמיכה", sup)):
        for n, z in enumerate(found[:2], 1):
            key, ordinal = f"level.{kind}{n}", "הקרוב" if n == 1 else "השני"
            edge = z["low"] if kind == "r" else z["high"]
            distance = max(0.0, (edge / close - 1) * 100 if kind == "r" else (1 - edge / close) * 100)
            f.add(f"{key}.low", z["low"], f"אזור ה{name} {ordinal}: גבול תחתון", "$")
            f.add(f"{key}.high", z["high"], f"אזור ה{name} {ordinal}: גבול עליון", "$")
            f.add(f"{key}.touches", z["touches"], f"אזור ה{name} {ordinal}: נגיעות", "", 0)
            f.add(f"{key}.distance_pct", distance, f"המרחק מהסגירה אל אזור ה{name} {ordinal}", "%", 1)
    s1, r1 = (sup[0] if sup else None), (res[0] if res else None)
    if s1 and s1["low"] <= close <= s1["high"]:
        position = "בתוך אזור התמיכה הקרוב"
    elif r1 and r1["low"] <= close <= r1["high"]:
        position = "בתוך אזור ההתנגדות הקרוב"
    elif s1 and r1:
        nearer = "להתנגדות" if r1["low"] - close < close - s1["high"] else "לתמיכה"
        position = f"בין התמיכה להתנגדות, קרוב יותר {nearer}"
    elif r1:
        position = "מתחת להתנגדות, בלי אזור תמיכה מסומן מתחת"
    elif s1:
        position = "מעל התמיכה, בלי אזור התנגדות מסומן מעל"
    else:
        position = ""
    f.add("position", position, "איפה הסגירה ביחס לאזורים בגרף")
    if r1:
        f.add("up.trigger", r1["high"], "תרחיש עולה: סגירה מעל הרמה הזו", "$")
        if len(res) > 1:
            f.add("up.next", res[1]["low"], "תרחיש עולה: הרמה הבאה בדרך", "$")
        if s1:
            f.add("up.cancel", s1["low"], "תרחיש עולה: סגירה מתחת לרמה הזו מבטלת אותו", "$")
    if s1:
        f.add("down.trigger", s1["low"], "תרחיש יורד: סגירה מתחת לרמה הזו", "$")
        if len(sup) > 1:
            f.add("down.next", sup[1]["high"], "תרחיש יורד: הרמה הבאה בדרך", "$")
        if r1:
            f.add("down.cancel", r1["high"], "תרחיש יורד: סגירה מעל הרמה הזו מבטלת אותו", "$")
    return f.out
