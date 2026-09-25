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

    # review round 1: the text spoke of the trend, the yearly extremes and a breakout the
    # chart did not show. The 50-day average; a 52-week high or low within reach that no
    # drawn zone holds; the outline of a pattern whose breakout is fresh or undone.
    facts = analysis.facts
    view["ma"] = {"type": "ma", "periods": [50]}
    for key, label, price_key in (("hi52", "שיא שנתי", "high_52w"), ("lo52", "שפל שנתי", "low_52w")):
        price = (facts.get(price_key) or {}).get("value")
        near_close = isinstance(price, (int, float)) and abs(price / close - 1) * 100 <= rules["extreme_draw_pct"]
        if near_close and zone_at(price) is None:
            view[key] = {"type": "extreme", "price": float(price), "label": label,
                         "day": (facts.get(f"{price_key}_day") or {}).get("value")}
    recent = []
    for key, item in drawings.items():
        since = (facts.get(f"{key}.sessions_since_breakout") or {}).get("value")
        fresh = isinstance(since, int) and since <= 2 * rules["event_fresh_sessions"]
        if item["type"] == "pattern" and item["family"] == "chart" and fresh:
            recent.append((since, key))
    if recent:
        key = min(recent)[1]
        view[key] = drawings[key]
    return view


def key_level_facts(analysis: Analysis, rules: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Facts for exactly what the chart shows, the two scenarios built on it, and the key
    event, so the written analysis quotes the reader's levels and never picks its own:
    - level.s1 / level.s2 (supports, nearest first), level.r1 / level.r2 (resistances):
      low, high, touches, and the close's distance to the zone's near edge;
    - position: where the close stands, in words;
    - up.* / down.*: trigger (a close beyond the nearest level), next (the next level at
      least scenario_min_gap_atr beyond the trigger), cancel (a close back through the
      triggered zone), each with its distance from the close in %; with no zone on a
      side, the 52-week high or low stands in, and at a new high there is no next level;
    - event: the one thing that matters most now (review round 1: every headline only
      restated where the price stood)."""
    from .facts import _Facts

    rules = rules or load_rules()
    facts = analysis.facts
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    view = simple_view(analysis, rules)
    close = float(value("close"))
    atr = float(value("atr") or math.nan)
    gap = rules["scenario_min_gap_atr"] * atr if math.isfinite(atr) else 0.0
    widest = rules["scenario_band_max_atr"] * atr if math.isfinite(atr) else math.inf
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
        position = "מתחת להתנגדות, בלי אזור תמיכה בגרף מתחת"
    elif s1:
        position = "מעל התמיכה; מעליה אין אזור התנגדות ממחירי העבר"
    else:
        position = ""
    f.add("position", position, "איפה הסגירה ביחס לאזורים בגרף")

    # the levels a scenario can use: every zone of the analysis, the big turning points and
    # the 52-week extreme; levels closer than scenario_min_gap_atr join into one band
    # (review round 1: a "next level" 0.3 ATR past its trigger, or 75% away)
    high52, low52 = value("high_52w"), value("low_52w")
    every = [v for v in analysis.drawings.values() if v.get("type") == "zone"]
    swings = (analysis.drawings.get("swings") or {}).get("points") or []

    def day_he(day: str) -> str:
        return f"{day[8:10]}/{day[5:7]}"

    raw_above = [(z["low"], z["high"], "אזור ההתנגדות") for z in every        # the one the price is in too
                 if z["low"] > close or (z["kind"] == "resistance" and z["low"] <= close <= z["high"])]
    raw_above += [(s["price"], s["price"], f"השיא מ-{day_he(s['day'])}") for s in swings
                  if s["kind"] == "H" and s["price"] > close]
    if isinstance(high52, (int, float)) and high52 > close:
        raw_above.append((high52, high52, "השיא השנתי"))
    raw_below = [(z["low"], z["high"], "אזור התמיכה") for z in every
                 if z["high"] < close or (z["kind"] == "support" and z["low"] <= close <= z["high"])]
    raw_below += [(s["price"], s["price"], f"השפל מ-{day_he(s['day'])}") for s in swings
                  if s["kind"] == "L" and s["price"] < close]
    if isinstance(low52, (int, float)) and low52 < close:
        raw_below.append((low52, low52, "השפל השנתי"))

    def bands(levels: list[tuple[float, float, str]], upward: bool) -> list[tuple[float, float, str]]:
        """Nearest first; a level within `gap` of the band before it joins that band."""
        out: list[list] = []
        for lo, hi, what in sorted(levels, key=lambda z: z[0] if upward else -z[1]):
            near = out and ((lo - out[-1][1] <= gap) if upward else (out[-1][0] - hi <= gap))
            if near and max(out[-1][1], hi) - min(out[-1][0], lo) <= widest:   # no chains
                band = out[-1]
                band[0], band[1] = min(band[0], lo), max(band[1], hi)
                if what in ("השיא השנתי", "השפל השנתי"):
                    band[2] = what                       # the strongest name wins
                elif band[2] != what and not band[2].startswith(("השיא השנתי", "השפל השנתי")):
                    band[2] = "רצועת ההתנגדות" if upward else "רצועת התמיכה"
                continue
            out.append([lo, hi, what])
        return [tuple(b) for b in out]

    above, below = bands(raw_above, True), bands(raw_below, False)
    # a zone around the close is where the price stands, not a scenario level
    inside = [z for z in every if z["low"] <= close <= z["high"]]

    def pct(level: float) -> float:
        return abs(level / close - 1) * 100

    def price_text(lo: float, hi: float) -> str:
        return f"{lo:.2f}-{hi:.2f}" if hi > lo else f"{hi:.2f}"

    if above:
        lo, hi, what = above[0]
        f.add("up.trigger", hi, f"תרחיש עלייה: סגירה מעל {what}", "$")
        f.add("up.trigger_pct", pct(hi), "תרחיש עלייה: המרחק מהסגירה לרמת הכניסה", "%", 1)
        f.add("up.trigger_what", what, "תרחיש עלייה: מה הרמה")
        # the next level at least `gap` past the trigger: a band's near edge, else its far one
        ahead = [(e, w if e == b_lo or b_lo == b_hi else f"ראש {w}") for b_lo, b_hi, w in above[1:]
                 for e in (b_lo, b_hi) if e - hi >= gap]
        if ahead:
            nlo, nwhat = ahead[0]
            f.add("up.next", nlo, f"תרחיש עלייה: הרמה הבאה ({nwhat})", "$")
            f.add("up.next_pct", pct(nlo), "תרחיש עלייה: המרחק מהסגירה לרמה הבאה", "%", 1)
            f.add("up.next_what", nwhat, "תרחיש עלייה: מה הרמה הבאה")
        else:
            f.add("up.no_next", "מעליה אין רמות ממחירי השנה האחרונה", "תרחיש עלייה: אין רמה הבאה")
        cancel = lo if lo < hi else (inside[0]["low"] if inside else hi)
        f.add("up.cancel", cancel, "תרחיש עלייה: סגירה חזרה מתחת לרמה הזו מבטלת אותו", "$")
    else:                                   # at a new high: nothing above from the past year
        f.add("up.no_next", "המחיר בשיא השנתי; מעליו אין רמות ממחירי השנה האחרונה",
              "תרחיש עלייה: אין רמה מעל")
    if below:
        lo, hi, what = below[0]
        f.add("down.trigger", lo, f"תרחיש ירידה: סגירה מתחת ל{what}", "$")
        f.add("down.trigger_pct", pct(lo), "תרחיש ירידה: המרחק מהסגירה לרמת הכניסה", "%", 1)
        f.add("down.trigger_what", what, "תרחיש ירידה: מה הרמה")
        ahead = [(e, w if e == b_hi or b_lo == b_hi else f"תחתית {w}") for b_lo, b_hi, w in below[1:]
                 for e in (b_hi, b_lo) if lo - e >= gap]
        if ahead:
            nhi, nwhat = ahead[0]
            f.add("down.next", nhi, f"תרחיש ירידה: הרמה הבאה ({nwhat})", "$")
            f.add("down.next_pct", pct(nhi), "תרחיש ירידה: המרחק מהסגירה לרמה הבאה", "%", 1)
            f.add("down.next_what", nwhat, "תרחיש ירידה: מה הרמה הבאה")
        else:
            f.add("down.no_next", "מתחתיה אין רמות ממחירי השנה האחרונה", "תרחיש ירידה: אין רמה הבאה")
        cancel = hi if lo < hi else (inside[0]["high"] if inside else lo)
        f.add("down.cancel", cancel, "תרחיש ירידה: סגירה חזרה מעל הרמה הזו מבטלת אותו", "$")
    event = key_event(facts, position, rules)
    if event:
        f.add("event", event[0], "האירוע המרכזי עכשיו")
        f.add("event.kind", event[1], "סוג האירוע המרכזי")
    return f.out


def key_event(facts: dict[str, dict[str, Any]], position: str,
              rules: dict[str, Any]) -> tuple[str, str] | None:
    """The one thing that matters most now, in words, by priority: a fresh pattern
    breakout (or one the price already undid), a 52-week high or low within reach, a big
    20-session move, the price inside a zone, else where it stands."""
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    fresh = rules["event_fresh_sessions"]
    patterns = sorted({k.split(".")[0] for k in facts if k.startswith("pat_") and k.endswith(".state")},
                      key=lambda p: value(f"{p}.sessions_since_breakout") or 0)
    for p in patterns:
        since, state, name = value(f"{p}.sessions_since_breakout"), value(f"{p}.state"), value(f"{p}.name")
        day = str(value(f"{p}.breakout_day") or "")
        when = f"{day[8:10]}/{day[5:7]}/{day[:4]}" if len(day) == 10 else ""
        up = value(f"{p}.direction") == "שורי"
        if isinstance(since, int) and "חזר" in str(state) and since <= 2 * fresh:
            return (f"המחיר חזר אל תוך {name} אחרי ה{'פריצה' if up else 'שבירה'} ב-{when}: "
                    f"ה{'פריצה' if up else 'שבירה'} מוטלת בספק", "failed_breakout")
        if isinstance(since, int) and since <= fresh:
            return (f"{'פריצה' if up else 'שבירה'} טרייה מתוך {name} ב-{when}, "
                    f"ב-{abs(value(f'{p}.close_vs_breakout_pct') or 0):.1f}% {'מעל' if up else 'מתחת ל'}קו", "fresh_breakout")
    near = rules["event_near_extreme_pct"]
    high_gap, low_gap = value("high_52w_distance_pct"), value("low_52w_distance_pct")
    if isinstance(high_gap, (int, float)) and high_gap <= near:
        return (("המחיר בשיא של השנה האחרונה" if high_gap <= 0.5 else
                 f"המחיר {high_gap:.1f}% מתחת לשיא השנתי ({value('high_52w'):.2f})"), "near_high")
    if isinstance(low_gap, (int, float)) and low_gap <= near:
        return (("המחיר בשפל של השנה האחרונה" if low_gap <= 0.5 else
                 f"המחיר {low_gap:.1f}% מעל השפל השנתי ({value('low_52w'):.2f})"), "near_low")
    move = value("change_20d_pct")
    if isinstance(move, (int, float)) and abs(move) >= rules["event_big_move_pct"]:
        return (f"{'עלייה' if move > 0 else 'ירידה'} של {abs(move):.1f}% ב-20 ימי המסחר האחרונים", "big_move")
    if position.startswith("בתוך"):
        return position, "inside_zone"
    return (position, "position") if position else None
