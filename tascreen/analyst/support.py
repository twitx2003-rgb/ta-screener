"""Support tests (owner, 2026-10-05): the price comes back down to a level it had left and
either holds there and bounces, or gives way. In the owner's words: strong support on the
150-day average with volume that is not weak, the same on the 20-day average, and
breakouts with the support tests that follow them, in every analysis.

One walk serves every level, given as one value per session: a moving average, the top of
a resistance zone the price broke out of, a pattern's breakout line extended with its
slope. Our rules, in ATRs of each session (rules.yaml), not Bulkowski's statistics:
- away: a test needs a session that closed at least `support_away_atr` above the level
  without touching it, since the last test: a test is a return to the level, not a price
  that never left it (a first try counted one hold several times while the price hovered
  just above it; a whole bar 1 ATR above left a normal uptrend without tests of its 20-day);
- touch: a session whose low comes within `support_touch_atr` above the level, or below it;
- held: after a touch, a close at least `support_bounce_atr` above the level before any
  close more than `support_break_atr` below it;
- broke: a close more than `support_break_atr` below the level (a gap through it counts);
- testing: a touch not decided by the last session. A test still undecided after
  `support_max_test_sessions` is dropped: the price is hugging the level, not testing it.
Volume, each session against the 50 before it: the bounce's busiest up-session (a close
above the one before) from the test's low to the deciding close. Under
`support_volume_min` it is weak, the threshold under which the analysis already calls a
session's volume low. The way down is told too: the mean from the last high to the low.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..indicators import sma

HELD, BROKE, TESTING = "held", "broke", "testing"


@dataclass(frozen=True)
class Test:
    touch: int                 # the first session that touched the level
    low: int                   # the test's lowest low
    end: int                   # the deciding session (the last session while testing)
    result: str                # HELD | BROKE | TESTING
    volume_at: int | None      # HELD: the bounce's busiest up-session
    volume_ratio: float        # its volume over the 50 sessions before it
    pullback_ratio: float      # the mean volume ratio from the last high to the test's low


def volume_ratios(volume: Any, sessions: int = 50) -> np.ndarray:
    """Each session's volume over the mean of up to `sessions` sessions before it."""
    v = pd.Series(np.asarray(volume, dtype=float))
    avg = v.shift(1).rolling(sessions, min_periods=1).mean()
    return (v / avg.where(avg > 0)).to_numpy(float)


def _mean(ratios: np.ndarray, a: int, b: int) -> float:
    seg = ratios[min(a, b):b + 1]
    seg = seg[np.isfinite(seg)]
    return float(seg.mean()) if len(seg) else math.nan


def walk(bars: pd.DataFrame, level: np.ndarray, atr: np.ndarray, start: int, rules: dict[str, Any],
         ratios: np.ndarray | None = None) -> list[Test]:
    """The tests of `level` as support from session `start` on, oldest first."""
    low, close = bars["low"].to_numpy(float), bars["close"].to_numpy(float)
    ratios = volume_ratios(bars["volume"]) if ratios is None else ratios
    away_k, touch_k = float(rules["support_away_atr"]), float(rules["support_touch_atr"])
    bounce_k, break_k = float(rules["support_bounce_atr"]), float(rules["support_break_atr"])
    longest = int(rules["support_max_test_sessions"])
    out: list[Test] = []
    away, peak = False, start
    touch = low_i = None
    for i in range(max(start, 1), len(close)):
        lv, a = float(level[i]), float(atr[i])
        if not (math.isfinite(lv) and math.isfinite(a) and a > 0):
            continue
        if touch is None:
            if close[i] < lv - break_k * a:
                if away:                                  # gave way without a touch first (a gap)
                    out.append(Test(i, i, i, BROKE, None, math.nan, _mean(ratios, peak + 1, i)))
                away = False
                continue
            if close[i] >= lv + away_k * a and low[i] > lv + touch_k * a:
                if not away or close[i] >= close[peak]:
                    peak = i
                away = True
            elif away and close[i] > close[peak]:
                peak = i
            if not (away and low[i] <= lv + touch_k * a):
                continue
            touch, low_i = i, i
        elif low[i] < low[low_i]:
            low_i = i
        if close[i] < lv - break_k * a:
            out.append(Test(touch, low_i, i, BROKE, None, math.nan, _mean(ratios, peak + 1, low_i)))
            touch, away = None, False
        elif close[i] >= lv + bounce_k * a:
            ups = [j for j in range(low_i, i + 1) if close[j] > close[j - 1] and math.isfinite(ratios[j])]
            at = max(ups, key=lambda j: ratios[j]) if ups else None
            out.append(Test(touch, low_i, i, HELD, at, float(ratios[at]) if at is not None else math.nan,
                            _mean(ratios, peak + 1, low_i)))
            touch, away, peak = None, False, i
        elif i - touch >= longest:
            touch, away = None, False                     # hugging the level, not a test
    if touch is not None:
        out.append(Test(touch, low_i, len(close) - 1, TESTING, None, math.nan, _mean(ratios, peak + 1, low_i)))
    return out


def volume_ok(ratio: float, rules: dict[str, Any]) -> bool:
    return math.isfinite(ratio) and ratio > float(rules["support_volume_min"])


# ------------------------------------------------------------------ facts
def _dm(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}"


def _to(what: str) -> str:
    """"ל" before a name: the definite article goes ("מתחת לאזור", not "מתחת להאזור")."""
    return "ל" + (what[1:] if what.startswith("ה") else what)


def _words(ratio: float) -> str | None:
    from .facts import volume_words          # facts imports this module

    return volume_words(ratio)


def _volume_phrase(ratio: float) -> str:
    words = _words(ratio)
    return f" בנפח {words}" if words else ""


def _test_facts(f, key: str, t: Test, day) -> None:
    """The details of one test, under `key` (a test of an average or a retest)."""
    f.add(f"{key}_low_day", day(t.low), f"היום שבו המחיר ירד הכי קרוב לרמה ({key})")
    if t.result == HELD and t.volume_at is not None:
        f.add(f"{key}_bounce_day", day(t.volume_at), f"יום הקפיצה מהרמה עם הנפח הגבוה ביותר ({key})")
        f.add(f"{key}_volume_ratio", t.volume_ratio, f"הנפח ביום הקפיצה חלקי ממוצע 50 יום ({key})", "x", 2)
        f.add(f"{key}_volume", _words(t.volume_ratio), f"הנפח ביום הקפיצה, במילים ({key})")
    if math.isfinite(t.pullback_ratio):
        f.add(f"{key}_pullback_volume_ratio", t.pullback_ratio,
              f"הנפח הממוצע בירידה אל הרמה חלקי ממוצע 50 יום ({key})", "x", 2)
        f.add(f"{key}_pullback_volume", _words(t.pullback_ratio), f"הנפח בירידה אל הרמה, במילים ({key})")


def ma_facts(f, bars: pd.DataFrame, n: int, atr: np.ndarray, rules: dict[str, Any], ratios: np.ndarray,
             day) -> None:
    """The n-day average as support (sma{n}.support*) and the latest close across it, up or
    down, with the tests that followed (sma{n}.cross*)."""
    close = bars["close"].to_numpy(float)
    level = sma(bars["close"], n).to_numpy(float)
    last = len(bars) - 1
    if last <= n or not math.isfinite(level[last]):
        return
    name = f"ממוצע {n} יום"
    lookback = int(rules["support_lookback_sessions"][n])
    start = max(n, last - lookback + 1)
    tests = walk(bars, level, atr, start, rules, ratios)
    fresh = int(rules["event_fresh_sessions"])
    k = f"sma{n}.support"
    f.add(f"{k}.since", day(start), f"מאז מתי נספרות בדיקות התמיכה של {name}")
    held = [t for t in tests if t.result == HELD]
    f.add(f"{k}.held", len(held), f"כמה פעמים {name} החזיק כתמיכה מאז {k}.since", "", 0)
    f.add(f"{k}.broke", sum(t.result == BROKE for t in tests), f"כמה פעמים המחיר נשבר מתחת {_to(name)} מאז", "", 0)
    decided = [t for t in tests if t.result != TESTING]
    if decided:
        lastd = decided[-1]
        streak = 0
        for t in reversed(decided):                    # holds in a row since the last break
            if t.result != HELD:
                break
            streak += 1
        if lastd.result == BROKE:
            strength = f"{name} נשבר כתמיכה בבדיקה האחרונה"
        elif streak >= int(rules["support_strong_holds"]) and volume_ok(lastd.volume_ratio, rules):
            strength = f"תמיכה חזקה: {name} החזיק {streak} פעמים ברצף, ובקפיצה האחרונה הנפח לא היה חלש"
        elif volume_ok(lastd.volume_ratio, rules):
            strength = f"{name} החזיק כתמיכה"
        else:
            strength = f"{name} החזיק כתמיכה, אבל בנפח חלש"
        f.add(f"{k}.strength", strength, f"כמה חזקה התמיכה של {name}")
        f.add(f"{k}.streak", streak, f"כמה פעמים ברצף {name} החזיק מאז השבירה האחרונה", "", 0)
    if tests:
        t = tests[-1]
        _test_facts(f, f"{k}.last", t, day)
        f.add(f"{k}.last_result", {HELD: "החזיק", BROKE: "נשבר", TESTING: "בבדיקה עכשיו"}[t.result],
              f"איך נגמרה הבדיקה האחרונה של {name}")
        if t.result == TESTING:
            state = f"המחיר בודק עכשיו את {name} כתמיכה" + (
                " (נגע בו היום)" if t.touch == last else f" (ירד אליו ב-{_dm(day(t.touch))})")
        elif last - t.end <= fresh and t.result == HELD:
            state = (f"{name} החזיק כתמיכה: המחיר ירד אליו ב-{_dm(day(t.low))} וקפץ ממנו"
                     + _volume_phrase(t.volume_ratio))
        elif last - t.end <= fresh:
            state = f"המחיר נשבר מתחת {_to(name)} ב-{_dm(day(t.end))}"
        else:
            state = None
        if state:
            f.add(f"{k}.state", state, f"{name} כתמיכה, עכשיו")
            f.add(f"{k}.sessions_ago", last - t.end, f"ימי מסחר מאז שהבדיקה האחרונה של {name} הוכרעה", "", 0)

    # the latest close across the average (a breakout above it or a break below it)
    span = int(rules["breakout_retest_sessions"])
    cross = None
    for j in range(last, max(n, last - span), -1):
        if all(math.isfinite(x) for x in (level[j], level[j - 1])) and (close[j] > level[j]) != (close[j - 1] > level[j - 1]):
            cross = j
            break
    if cross is None:
        return
    up = close[cross] > level[cross]
    c = f"sma{n}.cross"
    f.add(c, f"פריצה מעל {name}" if up else f"שבירה מתחת {_to(name)}", f"החצייה האחרונה של {name}")
    f.add(f"{c}_day", day(cross), f"יום החצייה של {name}")
    f.add(f"{c}_sessions_ago", last - cross, f"ימי מסחר מאז החצייה של {name}", "", 0)
    f.add(f"{c}_volume_ratio", float(ratios[cross]), f"הנפח ביום החצייה של {name} חלקי ממוצע 50 יום", "x", 2)
    f.add(f"{c}_volume", _words(float(ratios[cross])), f"הנפח ביום החצייה של {name}, במילים")
    if up:
        retest_facts(f, c, name, level, cross, bars, atr, rules, ratios, day)


def retest_facts(f, key: str, what: str, level: np.ndarray, at: int, bars: pd.DataFrame, atr: np.ndarray,
                 rules: dict[str, Any], ratios: np.ndarray, day) -> list[Test]:
    """The support tests after a breakout over `level` at session `at` (returned): {key}.retest
    says whether the price came back to the broken level and held, is on it now, broke back
    under it, or has not come back yet."""
    close = bars["close"].to_numpy(float)
    last = len(bars) - 1
    a = float(atr[last])
    if not (math.isfinite(level[last]) and math.isfinite(a)):
        return []
    tests = walk(bars, level, atr, at, rules, ratios)
    r = f"{key}.retest"
    if close[last] < level[last] - float(rules["support_break_atr"]) * a:
        text = f"המחיר נסגר שוב מתחת {_to(what)}: הפריצה לא החזיקה"
    elif not tests:
        if close[last] < level[last] + float(rules["support_away_atr"]) * a:
            return []                   # still beside the level: it never left, so nothing to test yet
        text = f"המחיר עוד לא חזר לבדוק את {what} מאז הפריצה"
    else:
        t = tests[-1]
        held = sum(x.result == HELD for x in tests)
        f.add(f"{r}_held", held, f"כמה בדיקות תמיכה אחרי הפריצה החזיקו ({key})", "", 0)
        _test_facts(f, r, t, day)
        f.add(f"{r}_sessions_ago", last - t.end, f"ימי מסחר מאז שבדיקת התמיכה הוכרעה ({key})", "", 0)
        if t.result == TESTING:
            text = f"המחיר חזר לבדוק את {what} מלמעלה ונמצא עליו עכשיו (בדיקת תמיכה אחרי הפריצה)"
        elif t.result == HELD:
            text = (f"המחיר חזר לבדוק את {what} ב-{_dm(day(t.low))}, החזיק מעליו וקפץ"
                    + _volume_phrase(t.volume_ratio))
        else:
            text = f"בדיקת התמיכה נכשלה: המחיר נסגר מתחת {_to(what)} ב-{_dm(day(t.end))}"
    f.add(r, text, f"בדיקת התמיכה אחרי הפריצה ({key})")
    return tests
