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

ZONE_UP, ZONE_DOWN = "אזור ההתנגדות", "אזור התמיכה"
HIGH52, LOW52 = "השיא השנתי", "השפל השנתי"


def note_parts(key: str) -> tuple[str, str]:
    """A zone note's word and number, kept apart so each renderer can order them."""
    if key == "poc":
        return "נפח מרבי", ""
    return "פיבונאצ'י", f"{int(key.rsplit('_', 1)[1]) / 10:g}%"


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

    lines = sorted((abs(item["price2"] - close), key) for key, item in drawings.items()
                   if item["type"] == "line" and item.get("touches", 0) >= 3)
    if lines and math.isfinite(atr) and lines[0][0] <= rules["view_trendline_atr"] * atr:
        view[lines[0][1]] = drawings[lines[0][1]]

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
    view["ma"] = {"type": "ma", "periods": [50, 150, 200]}    # the text names all three
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
        up_gap, down_gap = r1["low"] - close, close - s1["high"]
        if math.isfinite(atr) and abs(up_gap - down_gap) < rules["closer_min_atr"] * atr:
            position = "באמצע, בין התמיכה להתנגדות"
        else:
            position = f"בין התמיכה להתנגדות, קרוב יותר {'להתנגדות' if up_gap < down_gap else 'לתמיכה'}"
    elif r1:
        position = "מתחת להתנגדות, בלי אזור תמיכה בגרף מתחת"
    elif s1:
        position = "מעל התמיכה; מעליה אין אזור התנגדות ממחירי העבר"
    else:
        position = ""
    f.add("position", position, "איפה הסגירה ביחס לאזורים בגרף")

    # the levels a scenario can use: every zone of the analysis, the big turning points,
    # the 52-week extremes and a drawn trendline. Levels closer than scenario_min_gap_atr
    # join into one band up to scenario_band_max_atr wide (review round 1: a "next level"
    # 0.3 ATR past its trigger); touching ones always join (round 2: six zones 0.3 ATR
    # apart read as six levels)
    high52, low52 = value("high_52w"), value("low_52w")
    every = [v for v in analysis.drawings.values() if v.get("type") == "zone"]
    swings = (analysis.drawings.get("swings") or {}).get("points") or []
    merge_gap = rules["scenario_merge_atr"] * atr if math.isfinite(atr) else 0.0

    def day_he(day: str) -> str:
        return f"{day[8:10]}/{day[5:7]}"

    raw_above = [(z["low"], z["high"], ZONE_UP) for z in every        # the one the price is in too
                 if z["low"] > close or (z["kind"] == "resistance" and z["low"] <= close <= z["high"])]
    raw_above += [(s["price"], s["price"], f"השיא מ-{day_he(s['day'])}") for s in swings
                  if s["kind"] == "H" and s["price"] > close]
    if isinstance(high52, (int, float)) and high52 > close:
        raw_above.append((high52, high52, HIGH52))
    raw_below = [(z["low"], z["high"], ZONE_DOWN) for z in every
                 if z["high"] < close or (z["kind"] == "support" and z["low"] <= close <= z["high"])]
    raw_below += [(s["price"], s["price"], f"השפל מ-{day_he(s['day'])}") for s in swings
                  if s["kind"] == "L" and s["price"] < close]
    if isinstance(low52, (int, float)) and low52 < close:
        raw_below.append((low52, low52, LOW52))
    for item in view.values():
        if item.get("type") == "line":
            now = float(item["price2"])
            (raw_above if now > close else raw_below).append(
                (now, now, "קו ההתנגדות" if now > close else "קו התמיכה"))

    def rank(what: str) -> int:
        return 3 if what in (HIGH52, LOW52) else 2 if what in (ZONE_UP, ZONE_DOWN) else 1

    def bands(levels: list[tuple[float, float, str]], upward: bool) -> list[tuple[float, float, str]]:
        """Nearest first; a level close enough to the band before it joins that band."""
        out: list[list] = []
        for lo, hi, what in sorted(levels, key=lambda z: z[0] if upward else -z[1]):
            if out:
                apart = (lo - out[-1][1]) if upward else (out[-1][0] - hi)
                width = max(out[-1][1], hi) - min(out[-1][0], lo)
                if apart <= merge_gap or (apart <= gap and width <= widest):
                    band = out[-1]
                    band[0], band[1] = min(band[0], lo), max(band[1], hi)
                    if rank(what) > rank(band[2]):
                        band[2] = what                   # the strongest name wins
                    continue
            out.append([lo, hi, what])
        return [tuple(b) for b in out]

    above, below = bands(raw_above, True), bands(raw_below, False)
    # a zone around the close is where the price stands, not a scenario level
    inside = [z for z in every if z["low"] <= close <= z["high"]]
    cancel_gap = rules["scenario_cancel_min_atr"] * atr if math.isfinite(atr) else 0.0

    def pct(level: float) -> float:
        return abs(level / close - 1) * 100

    def edge_name(lo: float, hi: float, what: str, upward: bool) -> str:
        if what in (ZONE_UP, ZONE_DOWN) and hi > lo:
            return f"הקצה {'העליון' if upward else 'התחתון'} של {what}"
        return what

    def next_name(what: str) -> str:
        return f"{what} הבא" if what in (ZONE_UP, ZONE_DOWN) else what

    def beyond(upward: bool, trigger: float) -> tuple[float, str] | None:
        """No level of the past year beyond the trigger: a pattern's measured target, else
        a Fibonacci extension, each marked as a measure and not a forecast."""
        for key in sorted(k for k in facts if k.startswith("pat_") and k.endswith(".target")):
            p = key.split(".")[0]
            target, direction = value(key), value(f"{p}.direction")
            fresh = value(f"{p}.sessions_since_breakout")
            if (isinstance(target, (int, float)) and direction == ("שורי" if upward else "דובי")
                    and isinstance(fresh, int) and fresh <= 2 * rules["event_fresh_sessions"]
                    and not value(f"{p}.target_reached_day")
                    and (target - trigger >= gap if upward else trigger - target >= gap)):
                return float(target), "יעד התבנית לפי גובהה (לא תחזית)"
        if value("fib.direction") == ("עלייה" if upward else "ירידה"):
            for key in ("fib_ext_1272", "fib_ext_1618"):
                level = value(key)
                if isinstance(level, (int, float)) and (level - trigger >= gap if upward else trigger - level >= gap):
                    ratio = int(key.rsplit("_", 1)[1]) / 10
                    return float(level), f"הרחבת פיבונאצ'י {ratio:g}% (לא תחזית)"
        return None

    for side, upward, ahead, behind in (("up", True, above, below), ("down", False, below, above)):
        over, where = ("מעל ", "מעל הסגירה") if upward else ("מתחת ל", "מתחת לסגירה")
        if not ahead:
            if upward:
                f.add("up.no_next", "המחיר בשיא השנתי; מעליו אין רמות מהשנה האחרונה", "תרחיש עלייה: אין רמה מעל")
            else:
                f.add("down.no_next", "המחיר בשפל השנתי; מתחתיו אין רמות מהשנה האחרונה", "תרחיש ירידה: אין רמה מתחת")
            continue
        lo, hi, what = ahead[0]
        trigger = hi if upward else lo
        named = what[1:] if not upward and what.startswith("ה") else what
        f.add(f"{side}.trigger", trigger, f"תרחיש {'עלייה' if upward else 'ירידה'}: סגירה {over}{named}", "$")
        f.add(f"{side}.trigger_pct", pct(trigger), f"המרחק מהסגירה לרמת הכניסה ({where})", "%", 1)
        f.add(f"{side}.trigger_what", edge_name(lo, hi, what, upward), "מה רמת הכניסה")
        if len(ahead) > 1:
            n_lo, n_hi, n_what = ahead[1]
            near, far = (n_lo, n_hi) if upward else (n_hi, n_lo)
            f.add(f"{side}.next", near, f"הרמה הבאה ({next_name(n_what)}), הקצה הקרוב", "$")
            if n_hi > n_lo:
                f.add(f"{side}.next_far", far, f"הרמה הבאה ({next_name(n_what)}), הקצה הרחוק", "$")
            f.add(f"{side}.next_pct", pct(near), f"המרחק מהסגירה לרמה הבאה ({where})", "%", 1)
            f.add(f"{side}.next_what", next_name(n_what), "מה הרמה הבאה")
            f.add(f"{side}.room_pct", abs(near / trigger - 1) * 100, "המרחק מרמת הכניסה לרמה הבאה", "%", 1)
        else:
            measured = beyond(upward, trigger)
            if measured is not None:
                level, name = measured
                f.add(f"{side}.next", level, f"הרמה הבאה ({name})", "$")
                f.add(f"{side}.next_pct", pct(level), f"המרחק מהסגירה לרמה הבאה ({where})", "%", 1)
                f.add(f"{side}.next_what", name, "מה הרמה הבאה")
                f.add(f"{side}.room_pct", abs(level / trigger - 1) * 100, "המרחק מרמת הכניסה לרמה הבאה", "%", 1)
            elif what in (HIGH52, LOW52):
                f.add(f"{side}.no_next", f"{'מעליו' if upward else 'מתחתיו'} אין רמות מהשנה האחרונה",
                      "אין רמה הבאה")
            else:
                f.add(f"{side}.no_next", f"{'מעל ' if upward else 'מתחת ל'}רמה זו אין רמות מהשנה האחרונה", "אין רמה הבאה")
        # what cancels it: a real level at least scenario_cancel_min_atr back from the trigger
        # (review round 2: a cancel equal to its trigger, or 0.1 ATR from it)
        candidates = ([lo if upward else hi] if hi > lo else [])
        candidates += [z["low"] if upward else z["high"] for z in inside]
        candidates += [b[1] if upward else b[0] for b in behind]
        back = [(trigger - c) if upward else (c - trigger) for c in candidates]
        cancel = next((c for c, b in zip(candidates, back) if b >= cancel_gap), None)
        if cancel is None:                         # none a full ATR back: the first half an ATR back
            cancel = next((c for c, b in zip(candidates, back) if b >= cancel_gap / 2), None)
        if cancel is not None:
            f.add(f"{side}.cancel", cancel, f"סגירה חזרה {'מתחת ל' if upward else 'מעל '}רמה זו מבטלת את התרחיש", "$")
            f.add(f"{side}.risk_pct", abs(cancel / trigger - 1) * 100, "המרחק מרמת הכניסה לרמת הביטול", "%", 1)
    event = key_event(facts, position, rules, analysis.drawings)
    if event:
        f.add("event", event[0], "האירוע המרכזי עכשיו")
        f.add("event.kind", event[1], "סוג האירוע המרכזי")
    return f.out


def key_event(facts: dict[str, dict[str, Any]], position: str, rules: dict[str, Any],
              drawings: dict[str, dict[str, Any]] | None = None) -> tuple[str, str] | None:
    """The key event (_key_event), with a new 52-week high or low of the last sessions
    added when the event does not already say it."""
    event = _key_event(facts, position, rules, drawings)
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    if event is None or event[1] in ("new_high", "new_low") or "שנתי חדש" in event[0]:
        return event
    for word, ago_key, day_key, level_key in (("שיא", "high_52w_sessions_ago", "high_52w_day", "high_52w"),
                                              ("שפל", "low_52w_sessions_ago", "low_52w_day", "low_52w")):
        ago, day, level = value(ago_key), str(value(day_key) or ""), value(level_key)
        if isinstance(ago, int) and ago <= rules["new_extreme_sessions"] and isinstance(level, (int, float)):
            when = "היום" if ago == 0 else f"ב-{day[8:10]}/{day[5:7]}"
            return f"{event[0]}; {word} שנתי חדש {when} ({level:.2f})", event[1]
    return event


def _key_event(facts: dict[str, dict[str, Any]], position: str, rules: dict[str, Any],
               drawings: dict[str, dict[str, Any]] | None = None) -> tuple[str, str] | None:
    """The one thing that matters most now, in words, by priority: a fresh pattern
    breakout (or one the price undid, or is retesting), a zone the price just closed
    through, a new 52-week high or low, one within reach, a big 20-session move, a
    pullback to a rising 50-day average, a range, the price inside a zone, else where it
    stands (review round 2: new highs, zone breaks, pullbacks and ranges had no headline)."""
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    fresh = rules["event_fresh_sessions"]

    def dm(day: Any) -> str:
        day = str(day or "")
        return f"{day[8:10]}/{day[5:7]}" if len(day) == 10 else ""

    patterns = sorted({k.split(".")[0] for k in facts if k.startswith("pat_") and k.endswith(".state")},
                      key=lambda p: value(f"{p}.sessions_since_breakout") or 0)
    for p in patterns:
        since, state, name = value(f"{p}.sessions_since_breakout"), str(value(f"{p}.state")), value(f"{p}.name")
        up = value(f"{p}.direction") == "שורי"
        word = "פריצה" if up else "שבירה"
        when = dm(value(f"{p}.breakout_day"))
        if isinstance(since, int) and "נכשלה" in state and since <= 2 * fresh:
            return (f"תבנית {name} נכשלה: אחרי ה{word} ב-{when} המחיר חזר ועבר את רמת הביטול שלה", "failed_pattern")
        if isinstance(since, int) and "חזר אל תוך" in state and since <= 2 * fresh:
            return (f"המחיר חזר אל תוך תבנית {name} אחרי ה{word} ב-{when}: ה{word} מוטלת בספק", "failed_breakout")
        if isinstance(since, int) and "לבדוק" in state and since <= 2 * fresh:
            line = value(f"{p}.line_now")
            return (f"אחרי ה{word} מתבנית {name} ב-{when}, המחיר חזר לבדוק את קו ה{word} "
                    f"({line:.2f}) מ{'למעלה' if up else 'למטה'}", "retest")
        if since == 0:                           # review round 2: "stayed above" on day one
            line = value(f"{p}.line_now")
            return (f"המחיר סגר היום לראשונה {'מעל קו הפריצה' if up else 'מתחת לקו השבירה'} של תבנית {name}"
                    + (f" ({line:.2f})" if isinstance(line, (int, float)) else ""), "fresh_breakout")
        if isinstance(since, int) and since <= fresh:
            verb = "פרץ כלפי מעלה" if up else "שבר כלפי מטה"
            return (f"המחיר {verb} מתבנית {name} ב-{when}, ונמצא {abs(value(f'{p}.close_vs_breakout_pct') or 0):.1f}% "
                    f"{'מעל קו' if up else 'מתחת לקו'} ה{word}", "fresh_breakout")
    new_days = rules["new_extreme_sessions"]
    high_ago, low_ago = value("high_52w_sessions_ago"), value("low_52w_sessions_ago")
    new_high = isinstance(high_ago, int) and high_ago <= new_days
    new_low = isinstance(low_ago, int) and low_ago <= new_days

    def when_ago(ago: int, day: Any) -> str:
        return "היום" if ago == 0 else f"ב-{dm(day)}"

    change, atr, close = value("change_1d_pct"), value("atr"), value("close")
    if all(isinstance(x, (int, float)) for x in (change, atr, close)) and change:
        prev = close / (1 + change / 100)
        if abs(close - prev) >= atr:
            for k in sorted(k for k in facts if k.startswith("zone_") and k.endswith(".low")):
                z = k.rsplit(".", 1)[0]
                lo, hi = value(f"{z}.low"), value(f"{z}.high")
                if change < 0 and lo <= prev <= hi + 0.25 * atr and close < lo:
                    return (f"המחיר נדחה היום מאזור ההתנגדות שבין {lo:.2f} ל-{hi:.2f}: ירידה של "
                            f"{abs(change):.1f}%", "rejected")
                if change > 0 and lo - 0.25 * atr <= prev <= hi and close > hi:
                    return (f"המחיר קפץ היום מאזור התמיכה שבין {lo:.2f} ל-{hi:.2f}: עלייה של "
                            f"{change:.1f}%", "bounced")
    broken = sorted((value(f"{k.rsplit('.', 1)[0]}.broken_sessions_ago"), k.rsplit(".", 1)[0])
                    for k in facts if k.startswith("zone_") and k.endswith(".broken"))
    if broken:
        _, z = broken[0]
        up = "מעלה" in str(value(f"{z}.broken"))
        lo, hi = value(f"{z}.low"), value(f"{z}.high")
        text = (f"המחיר {'פרץ מעל ' if up else 'שבר מתחת ל'}אזור ה{'התנגדות' if up else 'תמיכה'} "
                f"שבין {lo:.2f} ל-{hi:.2f} ב-{dm(value(f'{z}.broken_day'))}")
        if up and new_high:
            text += f", ורשם שיא שנתי חדש {when_ago(high_ago, value('high_52w_day'))}"
        if not up and new_low:
            text += f", ורשם שפל שנתי חדש {when_ago(low_ago, value('low_52w_day'))}"
        return text, "zone_break"
    if new_high or new_low:
        up = new_high and (not new_low or high_ago <= low_ago)
        ago, level = (high_ago, value("high_52w")) if up else (low_ago, value("low_52w"))
        text = (f"{'שיא' if up else 'שפל'} שנתי חדש {when_ago(ago, value('high_52w_day' if up else 'low_52w_day'))} "
                f"({level:.2f})")
        gap = value("high_52w_distance_pct" if up else "low_52w_distance_pct")
        if isinstance(gap, (int, float)) and gap >= 0.5:
            text += f"; הסגירה {gap:.1f}% {'מתחתיו' if up else 'מעליו'}"
        if isinstance(change, (int, float)) and ago == 0 and (change > 0) != up and abs(change) >= 1:
            text += f", והיום נסגר ב{'עלייה' if change > 0 else 'ירידה'} של {abs(change):.1f}%"
        return text, "new_high" if up else "new_low"
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
    to_ma, ma = value("sma50.distance_atr"), value("sma50")
    if (value("sma50.direction") == "עולה" and isinstance(to_ma, (int, float))
            and 0 <= to_ma <= rules["pullback_ma50_atr"] and isinstance(ma, (int, float))):
        text = f"מגמת עלייה: המחיר ירד חזרה אל ממוצע 50 העולה ({ma:.2f}, {value('vs_sma50_pct'):.1f}% מתחת לסגירה)"
        if isinstance(high_gap, (int, float)):
            text += f", {high_gap:.1f}% מתחת לשיא השנתי"
        return text, "pullback_ma50"
    r_lo, r_hi = value("range.low"), value("range.high")
    if isinstance(r_lo, (int, float)) and isinstance(r_hi, (int, float)):
        months = round((value("range.sessions") or 120) / 21)
        return (f"דשדוש: המחיר נע בין {r_lo:.2f} ל-{r_hi:.2f} ב-{months} החודשים האחרונים, "
                "והממוצעים 50, 150 ו-200 צמודים זה לזה", "range")
    if position.startswith("בתוך"):
        return position, "inside_zone"
    return (position, "position") if position else None
