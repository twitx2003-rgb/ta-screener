"""The level map and the simple view: what the chart, the Pine Script, the level facts and
the two scenarios show (owner, 2026-09-24: "simple and to the point - support and
resistance, Fibonacci and the volume profile when needed"). The facts keep everything else
(trendlines, divergences, patterns, averages) for the written analysis.

Review round 3 (2026-09-26): the Levels line, the scenarios and the chart each had their own
list of levels, so a scenario could start at a zone the reader was never shown. Now one map
feeds all of them:
- the level map (`_plan`): every zone, the big turning points, the 52-week extremes and the
  drawn trendline, above and below the close, nearest first. Levels within
  scenario_min_gap_atr of each other join one band while it stays within
  scenario_band_max_atr (round 3: an unbounded chain of touching zones made one "zone" three
  ATRs wide);
- the chart and the Pine Script draw the first view_zones_each_side bands on each side
  (lvl_r1, lvl_r2, lvl_s1, lvl_s2), naming what else sits in each (the 52-week extreme, the
  trendline, an average, a pattern's line, a Fibonacci level, the POC);
- the level facts (level.r1 ...) and the scenarios quote those same bands; a scenario level
  no drawn band holds (a measured target, a cancel further back) is drawn as its own line;
- the Fibonacci retracements (38.2 / 50 / 61.8 only) while the price is inside a pullback of
  the last big move, and the volume profile when its POC is near the price.
"""
from __future__ import annotations

import math
from typing import Any

from . import load_rules
from .facts import Analysis

ZONE_UP, ZONE_DOWN = "אזור ההתנגדות", "אזור התמיכה"
HIGH52, LOW52 = "השיא השנתי", "השפל השנתי"
LINE_UP, LINE_DOWN = "קו ההתנגדות", "קו התמיכה"
FIB = "פיבונאצ׳י"
# what else sits in a band: as the chart labels it, and as the text names it
NOTE_WORDS = {"hi52": "שיא שנתי", "lo52": "שפל שנתי", "line": "קו מגמה", "pat": "קו התבנית",
              "sma50": "ממוצע 50 יום", "sma150": "ממוצע 150 יום", "sma200": "ממוצע 200 יום",
              "poc": "נפח מרבי"}
NOTE_TEXT = {**NOTE_WORDS, "hi52": HIGH52, "lo52": LOW52}
SHORT = {HIGH52: "שיא שנתי", LOW52: "שפל שנתי", LINE_UP: "קו התנגדות", LINE_DOWN: "קו תמיכה"}


def note_parts(key: str) -> tuple[str, str]:
    """A band note's words and number, kept apart so each renderer can order them."""
    if key in NOTE_WORDS:
        return NOTE_WORDS[key], ""
    return FIB, f"{int(key.rsplit('_', 1)[1]) / 10:g}%"


def _dm(day: Any) -> str:
    day = str(day or "")
    return f"{day[8:10]}/{day[5:7]}" if len(day) >= 10 else ""


def _plan(analysis: Analysis, rules: dict[str, Any]) -> dict[str, Any]:
    """The level map: {"above": bands, "below": bands (nearest first), "line": the drawn
    trendline's id or None, "scenarios": {"up": {...}, "down": {...}}}."""
    facts, drawings = analysis.facts, analysis.drawings
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    close = float(value("close"))
    atr = float(value("atr") or math.nan)
    known = math.isfinite(atr) and atr > 0
    unit = atr if known else 0.0
    gap = rules["scenario_min_gap_atr"] * unit
    widest = rules["scenario_band_max_atr"] * atr if known else math.inf
    fresh = rules["event_fresh_sessions"]

    # a line on its own side of the price only (round 3: a crossed line was drawn and named
    # by the scenarios as if it still held)
    lines = sorted((abs(float(item["price2"]) - close), key) for key, item in drawings.items()
                   if item.get("type") == "line" and item.get("touches", 0) >= 3
                   and (item.get("kind") == "resistance") == (float(item["price2"]) > close))
    line = lines[0][1] if lines and known and lines[0][0] <= rules["view_trendline_atr"] * atr else None

    def part(lo: float, hi: float, rank: float, key: str, what: str, day: Any, touches: Any = 0) -> dict:
        return {"low": float(lo), "high": float(hi), "rank": rank, "key": key, "what": what,
                "day": day, "touches": int(touches or 0)}

    above: list[dict] = []
    below: list[dict] = []
    for key, z in drawings.items():
        if z.get("type") != "zone":
            continue
        if z["low"] > close or (z["kind"] == "resistance" and z["low"] <= close <= z["high"]):
            above.append(part(z["low"], z["high"], 2, key, ZONE_UP, z.get("first_day"), z.get("touches")))
        elif z["high"] < close or (z["kind"] == "support" and z["low"] <= close <= z["high"]):
            below.append(part(z["low"], z["high"], 2, key, ZONE_DOWN, z.get("first_day"), z.get("touches")))
    for s in (drawings.get("swings") or {}).get("points") or []:
        price = float(s["price"])
        if s["kind"] == "H" and price > close:
            above.append(part(price, price, 1, "swing", f"השיא מ-{_dm(s['day'])}", s["day"]))
        elif s["kind"] == "L" and price < close:
            below.append(part(price, price, 1, "swing", f"השפל מ-{_dm(s['day'])}", s["day"]))
    high52, low52 = value("high_52w"), value("low_52w")
    if isinstance(high52, (int, float)) and high52 > close:
        above.append(part(high52, high52, 3, "hi52", HIGH52, value("high_52w_day")))
    if isinstance(low52, (int, float)) and low52 < close:
        below.append(part(low52, low52, 3, "lo52", LOW52, value("low_52w_day")))
    if line:
        now = float(drawings[line]["price2"])
        (above if now > close else below).append(
            part(now, now, 1.5, "line", LINE_UP if now > close else LINE_DOWN, drawings[line].get("day1")))

    def band(b: dict, upward: bool) -> dict[str, Any]:
        parts = b["parts"]
        keys = {p["key"] for p in parts}
        zones = [p for p in parts if p["key"].startswith("zone_")]
        if zones:
            name, label = (ZONE_UP, "התנגדות") if upward else (ZONE_DOWN, "תמיכה")
        else:
            name = max(parts, key=lambda p: p["rank"])["what"]
            label = SHORT.get(name, name.replace("השיא מ-", "שיא ").replace("השפל מ-", "שפל "))
        named = {HIGH52: "hi52", LOW52: "lo52", LINE_UP: "line", LINE_DOWN: "line"}.get(name)
        notes = [k for k in ("hi52" if upward else "lo52", "line") if k in keys and k != named]
        days = sorted(str(p["day"]) for p in parts if p.get("day"))
        return {"type": "zone", "kind": "resistance" if upward else "support",
                "low": b["low"], "high": b["high"], "name": name, "label": label, "notes": notes,
                "touches": sum(p["touches"] for p in zones), "first_day": days[0] if days else None,
                "zones": [p["key"] for p in zones]}

    def bands(parts: list[dict], upward: bool) -> list[dict[str, Any]]:
        """Nearest first; a level close enough to the band before it joins that band while
        the band stays within scenario_band_max_atr."""
        out: list[dict] = []
        for p in sorted(parts, key=lambda p: p["low"] if upward else -p["high"]):
            if out:
                b = out[-1]
                apart = (p["low"] - b["high"]) if upward else (b["low"] - p["high"])
                width = max(b["high"], p["high"]) - min(b["low"], p["low"])
                if apart <= gap and width <= widest:
                    b["parts"].append(p)
                    b["low"], b["high"] = min(b["low"], p["low"]), max(b["high"], p["high"])
                    continue
            out.append({"low": p["low"], "high": p["high"], "parts": [p]})
        return [band(b, upward) for b in out]

    up_bands, down_bands = bands(above, True), bands(below, False)
    # what else sits in the nearest bands: an average, a fresh pattern's line (round 3:
    # "the support holds the 50-day average" was never said)
    tol = rules["band_confluence_atr"] * unit
    patterns = sorted({k.split(".")[0] for k in facts if k.startswith("pat_") and k.endswith(".line_now")})
    extras = [(f"sma{n}", value(f"sma{n}")) for n in (50, 150, 200)]
    extras += [("pat", value(f"{p}.line_now")) for p in patterns
               if isinstance(value(f"{p}.sessions_since_breakout"), int)
               and value(f"{p}.sessions_since_breakout") <= 2 * fresh]
    for b in up_bands[:2] + down_bands[:2]:
        for key, price in extras:
            if isinstance(price, (int, float)) and b["low"] - tol <= price <= b["high"] + tol and key not in b["notes"]:
                b["notes"].append(key)

    # a fresh breakout the price still holds near its line: "the breakout holds while the
    # close stays above the line" (round 3: the scenarios ignored today's breakout)
    hold: dict[str, tuple[float, str]] = {}
    for p in patterns:
        since, level, state = value(f"{p}.sessions_since_breakout"), value(f"{p}.line_now"), str(value(f"{p}.state") or "")
        up = value(f"{p}.direction") == "שורי"
        if (isinstance(since, int) and since <= rules["hold_sessions"] and isinstance(level, (int, float)) and known
                and "נכשלה" not in state and "חזר אל תוך" not in state
                and (level < close if up else level > close) and abs(close - level) <= rules["hold_max_atr"] * atr):
            hold.setdefault("up" if up else "down", (float(level), str(value(f"{p}.name"))))

    def beyond(upward: bool, start: float) -> tuple[float, str, str] | None:
        """No level of the past year beyond `start`: the nearest measured one, a fresh
        pattern's target or a Fibonacci extension (round 3: a target 15% away was chosen
        over an extension 4% away)."""
        found = []
        for key in sorted(k for k in facts if k.startswith("pat_") and k.endswith(".target")):
            p = key.split(".")[0]
            target, since = value(key), value(f"{p}.sessions_since_breakout")
            if (isinstance(target, (int, float)) and value(f"{p}.direction") == ("שורי" if upward else "דובי")
                    and isinstance(since, int) and since <= 2 * fresh and not value(f"{p}.target_reached_day")
                    and (target - start if upward else start - target) >= gap):
                found.append((float(target), "יעד לפי גובה התבנית", "יעד התבנית"))
        if value("fib.direction") == ("עלייה" if upward else "ירידה"):
            for key in ("fib_ext_1272", "fib_ext_1618"):
                level = value(key)
                if isinstance(level, (int, float)) and (level - start if upward else start - level) >= gap:
                    ratio = f"{int(key.rsplit('_', 1)[1]) / 10:g}%"
                    found.append((float(level), f"מחושבת מאורך התנועה האחרונה לפי {FIB} {ratio}", f"{FIB} {ratio}"))
        return min(found, key=lambda x: abs(x[0] - start)) if found else None

    def edge_what(b: dict, upward: bool) -> str:
        edge, extreme = (b["high"], high52) if upward else (b["low"], low52)
        if isinstance(extreme, (int, float)) and abs(edge - float(extreme)) < 1e-9:
            return HIGH52 if upward else LOW52
        if b["name"] in (ZONE_UP, ZONE_DOWN):
            return "הקצה העליון של ההתנגדות הקרובה" if upward else "הקצה התחתון של התמיכה הקרובה"
        return b["name"]

    def next_what(b: dict, upward: bool) -> str:
        if b["name"] in (ZONE_UP, ZONE_DOWN):
            return "ההתנגדות הבאה" if upward else "התמיכה הבאה"
        return b["name"]

    scenarios: dict[str, dict[str, Any]] = {}
    for side, upward, ahead, behind in (("up", True, up_bands, down_bands), ("down", False, down_bands, up_bands)):
        s: dict[str, Any] = {}
        if side in hold:
            level, pattern = hold[side]
            s.update(mode="hold", trigger=level, pattern=pattern)
            candidates = [b for b in ahead if (b["low"] > close if upward else b["high"] < close)]
        elif ahead:
            b = ahead[0]
            trigger = b["high"] if upward else b["low"]
            s.update(mode="cross", trigger=trigger, trigger_what=edge_what(b, upward))
            candidates = ahead[1:]
            # what cancels it: a close back into the band it crossed (a failed break); a
            # band too thin for that: half a day's average range back (round 2: a cancel on
            # its trigger; round 3: a level further back sat on the far side of the close)
            if known and b["high"] - b["low"] >= rules["scenario_cancel_band_atr"] * atr:
                s.update(cancel=b["low"] if upward else b["high"], cancel_what="inside")
            elif known:
                back = rules["scenario_cancel_min_atr"] * atr
                s.update(cancel=round(trigger - back if upward else trigger + back, 2), cancel_what="offset",
                         cancel_label="ביטול")
        else:
            s.update(mode="none", no_next=(f"המחיר בשיא השנתי; מעליו אין רמות מהשנה האחרונה" if upward else
                                            f"המחיר בשפל השנתי; מתחתיו אין רמות מהשנה האחרונה"))
            scenarios[side] = s
            continue
        nxt = candidates[0] if candidates else None
        if nxt is not None:
            near, far = (nxt["low"], nxt["high"]) if upward else (nxt["high"], nxt["low"])
            s.update(next=near, next_what=next_what(nxt, upward), next_label=nxt["label"], next_band=nxt)
            if abs(far - near) > 1e-9:
                s["next_far"] = far                  # the far edge of the same band (round 3)
        else:
            measured = beyond(upward, s["trigger"] if s["mode"] == "cross" else close)
            if measured is not None:
                s.update(next=measured[0], next_what=measured[1], next_label=measured[2])
            elif s.get("trigger_what") in (HIGH52, LOW52):
                s["no_next"] = "מעליו אין רמות מהשנה האחרונה" if upward else "מתחתיו אין רמות מהשנה האחרונה"
            else:
                s["no_next"] = "מעל רמה זו אין רמות מהשנה האחרונה" if upward else "מתחת לרמה זו אין רמות מהשנה האחרונה"
        scenarios[side] = s
    return {"above": up_bands, "below": down_bands, "line": line, "scenarios": scenarios}


def simple_view(analysis: Analysis, rules: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """{id: drawing} for the simple picture. Bands carry `notes` (keys: "hi52", "line",
    "sma50", "pat", "fib_500", "poc", ...); "fib" and "vp", when the analysis has them, carry
    `show` (whether they matter now): the Pine Script offers them as switches, the chart
    draws only the shown ones."""
    rules = rules or load_rules()
    plan = _plan(analysis, rules)
    drawings, facts = analysis.drawings, analysis.facts
    close = float(facts["close"]["value"])
    atr = float((facts.get("atr") or {}).get("value") or math.nan)
    near = rules["view_confluence_atr"] * atr if math.isfinite(atr) else 0.0
    view: dict[str, dict[str, Any]] = {}
    each = int(rules["view_zones_each_side"])
    for side, found in (("r", plan["above"]), ("s", plan["below"])):
        for n, band in enumerate(found[:each], 1):
            view[f"lvl_{side}{n}"] = {**band, "notes": list(band["notes"])}

    def zone_at(price: float) -> dict[str, Any] | None:
        for item in view.values():
            if item["type"] == "zone" and item["low"] - near <= price <= item["high"] + near:
                return item
        return None

    if plan["line"]:
        view[plan["line"]] = drawings[plan["line"]]

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
    # chart did not show. The averages; a 52-week high or low within reach that no drawn
    # band holds; the outline of a pattern whose breakout is fresh or undone.
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

    # every scenario level is on the chart (round 3: next levels, measured targets and
    # cancels further back were named in the text and missing from the chart)
    line_price = float(drawings[plan["line"]]["price2"]) if plan["line"] else None
    for side in ("up", "down"):
        s = plan["scenarios"][side]
        for which in ("next", "cancel"):
            price = s.get(which)
            if not isinstance(price, (int, float)) or zone_at(price) is not None:
                continue
            if which == "cancel" and s.get("cancel_what") == "offset":
                continue                        # not a level: the text says how it is measured
            if line_price is not None and abs(price - line_price) < 1e-9:
                continue
            if any(v.get("type") == "extreme" and abs(v["price"] - price) < 1e-9 for v in view.values()):
                continue
            band = s.get(f"{which}_band")
            if band is not None and band["high"] > band["low"]:
                view[f"{side}_{which}"] = {**band, "notes": list(band["notes"])}
            else:
                view[f"{side}_{which}"] = {"type": "level", "price": float(price), "label": s.get(f"{which}_label") or "",
                                           "kind": "resistance" if price > close else "support"}
    return view


def note_labels(notes: list[str]) -> list[tuple[str, str]]:
    """A band's notes as the chart labels them, the averages as one (round 3: four lines
    of averages in one label)."""
    periods = [k[3:] for k in notes if k.startswith("sma")]
    out = [note_parts(k) for k in notes if not k.startswith("sma")]
    if len(periods) == 3:
        out.append(("שלושת הממוצעים", ""))
    elif len(periods) == 2:
        out.append((f"ממוצעי {periods[0]} ו-{periods[1]}", ""))
    elif periods:
        out.append((f"ממוצע {periods[0]} יום", ""))
    return out


def _note_texts(notes: list[str]) -> list[str]:
    """What else is in a band, for the text; the averages as one item (round 3: "ממוצע 50
    יום, ממוצע 150 יום וממוצע 200 יום")."""
    periods = [k[3:] for k in notes if k.startswith("sma")]
    out = [NOTE_TEXT[k] for k in notes if k in NOTE_TEXT and not k.startswith("sma")]
    if len(periods) == 3:
        out.append("שלושת הממוצעים")
    elif len(periods) == 2:
        out.append(f"ממוצעי {periods[0]} ו-{periods[1]} יום")
    elif periods:
        out.append(f"ממוצע {periods[0]} יום")
    return out


def _flipped(facts: dict[str, dict[str, Any]], band: dict[str, Any]) -> str | None:
    """A band holding a zone the price just closed through: its new role."""
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    for z in band["zones"]:
        broken = value(f"{z}.broken")
        if broken:
            day = _dm(value(f"{z}.broken_day"))
            # a possibility, not a fact (review round 3: a zone broken that same day was
            # told as plain support)
            when = "היום" if value(f"{z}.broken_sessions_ago") == 0 else f"ב-{day}"
            return (f"התנגדות שנפרצה {when} ועשויה לשמש עכשיו תמיכה" if "מעלה" in str(broken) else
                    f"תמיכה שנשברה {when} ועשויה לשמש עכשיו התנגדות")
    return None


def key_level_facts(analysis: Analysis, rules: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Facts for exactly what the chart shows, the two scenarios built on it, and the key
    event, so the written analysis quotes the reader's levels and never picks its own:
    - level.r1 / level.r2 (resistances, nearest first), level.s1 / level.s2 (supports): the
      bands of the level map, with low, high, what, what else is in them, touches, the
      close's distance to the near edge, and a flipped role;
    - position: where the close stands, in words;
    - up.* / down.*: a close beyond the nearest band's far edge (trigger), the next band's
      near and far edges (next, next_far) or a measured level, and a close back into the
      crossed band (cancel), each with its distance; or, right after a breakout, the line
      the breakout holds above (hold);
    - event: the one thing that matters most now (review round 1: every headline only
      restated where the price stood)."""
    from .facts import _Facts

    rules = rules or load_rules()
    facts = analysis.facts
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    plan = _plan(analysis, rules)
    close = float(value("close"))
    atr = float(value("atr") or math.nan)
    f = _Facts()
    for kind, found in (("r", plan["above"]), ("s", plan["below"])):
        name = "ההתנגדות" if kind == "r" else "התמיכה"
        for n, b in enumerate(found[:2], 1):
            key, ordinal = f"level.{kind}{n}", "הקרובה" if n == 1 else "השנייה"
            edge = b["low"] if kind == "r" else b["high"]
            distance = max(0.0, (edge / close - 1) * 100 if kind == "r" else (1 - edge / close) * 100)
            f.add(f"{key}.low", b["low"], f"{name} {ordinal}: גבול תחתון", "$")
            f.add(f"{key}.high", b["high"], f"{name} {ordinal}: גבול עליון", "$")
            f.add(f"{key}.what", b["name"], f"{name} {ordinal}: מה היא")
            if b["notes"]:
                f.add(f"{key}.includes", ", ".join(_note_texts(b["notes"])), f"{name} {ordinal}: מה עוד נמצא בה")
            if b["touches"]:
                f.add(f"{key}.touches", b["touches"], f"{name} {ordinal}: נגיעות", "", 0)
            f.add(f"{key}.distance_pct", distance, f"המרחק מהסגירה אל {name} {ordinal} (הקצה הקרוב)", "%", 1)
            flipped = _flipped(facts, b)
            if flipped:
                f.add(f"{key}.flipped", flipped, f"{name} {ordinal}: אזור שהמחיר חצה לאחרונה")
    s1 = plan["below"][0] if plan["below"] else None
    r1 = plan["above"][0] if plan["above"] else None
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
        position = "מתחת להתנגדות, בלי רמת תמיכה מהשנה האחרונה מתחת"
    elif s1:
        position = "מעל התמיכה; מעליה אין התנגדות מהשנה האחרונה"
    else:
        position = ""
    f.add("position", position, "איפה הסגירה ביחס לרמות בגרף")

    def pct(level: float) -> float:
        return abs(level / close - 1) * 100

    for side, upward in (("up", True), ("down", False)):
        s = plan["scenarios"][side]
        where = "מעל הסגירה" if upward else "מתחת לסגירה"
        title = f"תרחיש {'עלייה' if upward else 'ירידה'}"
        if s["mode"] == "none":
            f.add(f"{side}.no_next", s["no_next"], f"{title}: אין רמה מעבר")
            continue
        trigger = s["trigger"]
        if s["mode"] == "hold":
            word = "הפריצה" if upward else "השבירה"
            f.add(f"{side}.hold", trigger, f"{title}: {word} נשמרת כל עוד הסגירה {'מעל' if upward else 'מתחת ל'}קו זה", "$")
            f.add(f"{side}.hold_pattern", s["pattern"], f"{title}: התבנית שממנה {word}")
            f.add(f"{side}.hold_pct", pct(trigger), f"המרחק מהסגירה לקו ה{word[1:]} ({'מתחת לסגירה' if upward else 'מעל הסגירה'})", "%", 1)
        else:
            what = s["trigger_what"]
            named = what[1:] if not upward and what.startswith("ה") else what
            f.add(f"{side}.trigger", trigger, f"{title}: סגירה {'מעל ' if upward else 'מתחת ל'}{named}", "$")
            f.add(f"{side}.trigger_pct", pct(trigger), f"המרחק מהסגירה לרמת ההפעלה ({where})", "%", 1)
            f.add(f"{side}.trigger_what", what, "מה רמת ההפעלה")
        if isinstance(s.get("next"), (int, float)):
            f.add(f"{side}.next", s["next"], f"הרמה הבאה ({s['next_what']}), הקצה הקרוב", "$")
            if isinstance(s.get("next_far"), (int, float)):
                f.add(f"{side}.next_far", s["next_far"], f"הרמה הבאה ({s['next_what']}), הקצה הרחוק", "$")
            f.add(f"{side}.next_pct", pct(s["next"]), f"המרחק מהסגירה לרמה הבאה ({where})", "%", 1)
            f.add(f"{side}.next_what", s["next_what"], "מה הרמה הבאה")
            if s["mode"] == "cross":
                f.add(f"{side}.room_pct", abs(s["next"] / trigger - 1) * 100, "המרחק מרמת ההפעלה לרמה הבאה", "%", 1)
        elif s.get("no_next"):
            f.add(f"{side}.no_next", s["no_next"], f"{title}: אין רמה הבאה")
        if isinstance(s.get("cancel"), (int, float)):
            f.add(f"{side}.cancel", s["cancel"], f"סגירה חוזרת {'מתחת ל' if upward else 'מעל '}רמה זו מבטלת את התרחיש", "$")
            f.add(f"{side}.cancel_what", {"inside": "חזרה אל תוך האזור", "offset": "חצי מהתנודה היומית הממוצעת"}.get(
                s["cancel_what"], s["cancel_what"]), "מה רמת הביטול")
            f.add(f"{side}.risk_pct", abs(s["cancel"] / trigger - 1) * 100, "המרחק מרמת ההפעלה לרמת הביטול", "%", 1)
    event = key_event(facts, position, rules, analysis.drawings)
    if event:
        f.add("event", event[0], "האירוע המרכזי עכשיו")
        f.add("event.kind", event[1], "סוג האירוע המרכזי")
    return f.out


def _volume_words(ratio: Any) -> str:
    """A session's volume against the average, for an event (round 3: an ordinary volume
    was told as confirmation, a very high one was left out)."""
    if not isinstance(ratio, (int, float)) or not math.isfinite(ratio):
        return ""
    if ratio >= 1.5:
        return f", בנפח גבוה פי {ratio:.1f} מהממוצע"
    if ratio < 0.8:
        return ", בנפח נמוך מהממוצע"
    return ""


def key_event(facts: dict[str, dict[str, Any]], position: str, rules: dict[str, Any],
              drawings: dict[str, dict[str, Any]] | None = None) -> tuple[str, str] | None:
    """The key event (_key_event), with a new 52-week high or low of the last sessions
    added when the event does not already say it."""
    event = _key_event(facts, position, rules, drawings)
    value = lambda key: (facts.get(key) or {}).get("value")          # noqa: E731
    if event is None or event[1] in ("new_high", "new_low") or "שנתי חדש" in event[0]:
        return event
    change = value("change_1d_pct")
    for word, ago_key, day_key, level_key, gap_key, up in (
            ("שיא", "high_52w_sessions_ago", "high_52w_day", "high_52w", "high_52w_distance_pct", True),
            ("שפל", "low_52w_sessions_ago", "low_52w_day", "low_52w", "low_52w_distance_pct", False)):
        ago, day, level, gap = value(ago_key), str(value(day_key) or ""), value(level_key), value(gap_key)
        if isinstance(ago, int) and ago <= rules["new_extreme_sessions"] and isinstance(level, (int, float)):
            # touched during the day, closed the other way (round 3: "a new low today" on a
            # day that closed up 2%)
            if (ago == 0 and isinstance(change, (int, float)) and (change < 0 if up else change > 0)
                    and isinstance(gap, (int, float)) and gap >= 0.5):
                return (f"{event[0]}; היום נגע ב{word} שנתי חדש ({level:.2f}) ונסגר ב{'ירידה' if up else 'עלייה'} "
                        f"של {abs(change):.1f}%", event[1])
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
    dm = _dm

    patterns = sorted({k.split(".")[0] for k in facts if k.startswith("pat_") and k.endswith(".state")},
                      key=lambda p: value(f"{p}.sessions_since_breakout") or 0)
    for p in patterns:
        since, state, name = value(f"{p}.sessions_since_breakout"), str(value(f"{p}.state")), value(f"{p}.name")
        up = value(f"{p}.direction") == "שורי"
        word = "פריצה" if up else "שבירה"
        when = dm(value(f"{p}.breakout_day"))
        volume = _volume_words(value(f"{p}.breakout_volume_ratio"))
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
            return (f"המחיר נסגר היום לראשונה {'מעל קו הפריצה' if up else 'מתחת לקו השבירה'} של תבנית {name}"
                    + (f" ({line:.2f})" if isinstance(line, (int, float)) else "") + volume, "fresh_breakout")
        if isinstance(since, int) and since <= fresh:
            return (f"{word} כלפי {'מעלה' if up else 'מטה'} מתבנית {name} ב-{when}{volume}; המחיר "
                    f"{abs(value(f'{p}.close_vs_breakout_pct') or 0):.1f}% {'מעל קו' if up else 'מתחת לקו'} ה{word}",
                    "fresh_breakout")
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
                    return (f"המחיר נבלם היום באזור ההתנגדות שבין {lo:.2f} ל-{hi:.2f} וירד "
                            f"{abs(change):.1f}%", "rejected")
                if change > 0 and lo - 0.25 * atr <= prev <= hi and close > hi:
                    return (f"המחיר קפץ היום מאזור התמיכה שבין {lo:.2f} ל-{hi:.2f} ועלה "
                            f"{change:.1f}%", "bounced")
    broken = sorted((value(f"{k.rsplit('.', 1)[0]}.broken_sessions_ago"), k.rsplit(".", 1)[0])
                    for k in facts if k.startswith("zone_") and k.endswith(".broken"))
    if broken:
        _, z = broken[0]
        up = "מעלה" in str(value(f"{z}.broken"))
        lo, hi = value(f"{z}.low"), value(f"{z}.high")
        text = (f"המחיר {'פרץ מעל' if up else 'נסגר מתחת ל'}אזור ה{'התנגדות' if up else 'תמיכה'} "
                f"שבין {lo:.2f} ל-{hi:.2f} ב-{dm(value(f'{z}.broken_day'))}")
        text = text.replace("פרץ מעלאזור", "פרץ מעל אזור")
        text += _volume_words(value(f"{z}.broken_volume_ratio"))
        if up and new_high:
            text += f", ורשם שיא שנתי חדש {when_ago(high_ago, value('high_52w_day'))}"
        if not up and new_low:
            text += f", ורשם שפל שנתי חדש {when_ago(low_ago, value('low_52w_day'))}"
        return text, "zone_break"
    if new_high or new_low:
        up = new_high and (not new_low or high_ago <= low_ago)
        ago, level = (high_ago, value("high_52w")) if up else (low_ago, value("low_52w"))
        gap = value("high_52w_distance_pct" if up else "low_52w_distance_pct")
        if (ago == 0 and isinstance(change, (int, float)) and (change > 0) != up and abs(change) >= 1
                and isinstance(gap, (int, float)) and gap >= 0.5):
            return (f"היום נגע ב{'שיא' if up else 'שפל'} שנתי חדש ({level:.2f}) ונסגר "
                    f"{gap:.1f}% {'מתחתיו' if up else 'מעליו'}, ב{'עלייה' if change > 0 else 'ירידה'} של "
                    f"{abs(change):.1f}%", "new_high" if up else "new_low")
        text = (f"{'שיא' if up else 'שפל'} שנתי חדש {when_ago(ago, value('high_52w_day' if up else 'low_52w_day'))} "
                f"({level:.2f})")
        if isinstance(gap, (int, float)) and gap >= 0.5:
            text += f"; הסגירה {gap:.1f}% {'מתחתיו' if up else 'מעליו'}"
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
        text = f"המחיר חזר אל ממוצע 50 יום העולה ({ma:.2f}, {value('vs_sma50_pct'):.1f}% מתחת לסגירה)"
        if isinstance(high_gap, (int, float)):
            text += f", {high_gap:.1f}% מתחת לשיא השנתי"
        return text, "pullback_ma50"
    r_lo, r_hi = value("range.low"), value("range.high")
    if isinstance(r_lo, (int, float)) and isinstance(r_hi, (int, float)):
        months = round((value("range.sessions") or 120) / 21)
        return (f"המחיר מדשדש בין {r_lo:.2f} ל-{r_hi:.2f} ב-{months} החודשים האחרונים, "
                "והממוצעים של 50, 150 ו-200 יום צמודים זה לזה", "range")
    if position.startswith("בתוך"):
        return position, "inside_zone"
    return (position, "position") if position else None
