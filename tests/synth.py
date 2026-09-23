"""Synthetic daily bars shaped like textbook patterns. All values are made up."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from tascreen.contracts import canonical_timestamps
from tascreen.market_hours import is_trading_day


def sessions(n: int, first: date = date(2025, 1, 2)) -> list[date]:
    out, day = [], first
    while len(out) < n:
        if is_trading_day(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def frame(rows: list[tuple[float, float, float, float]], volume=None, symbol="TEST:SYN") -> pd.DataFrame:
    days = sessions(len(rows))
    df = pd.DataFrame({
        "timestamp": canonical_timestamps(pd.Series(
            [datetime.combine(d, time(13, 30), tzinfo=timezone.utc) for d in days])),
        "symbol": symbol,
        "open": [r[0] for r in rows], "high": [r[1] for r in rows],
        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
        "volume": volume if volume is not None else [1_000_000.0] * len(rows),
    })
    return df


def from_knots(knots: list[tuple[int, float]], *, noise: float = 0.1, wiggle: float = 0.3,
               seed: int = 7) -> pd.DataFrame:
    """Closes follow straight lines between (session, price) knots, plus a little
    noise; each bar opens at the previous close and has `wiggle` of shadow."""
    rng = np.random.default_rng(seed)
    xs, ys = zip(*knots)
    n = xs[-1] + 1
    closes = np.interp(np.arange(n), xs, ys) + rng.normal(0, noise, n)
    closes[list(xs)] = ys                     # knots exact, so the pattern is exact
    rows, prev = [], closes[0]
    for c in closes:
        o = prev
        rows.append((o, max(o, c) + wiggle, min(o, c) - wiggle, c))
        prev = c
    return frame(rows)


def candles(lead_in: int, pattern: list[tuple[float, float, float, float]], *, trend: str,
            start: float = 100.0, step: float = 0.6) -> pd.DataFrame:
    """`lead_in` ordinary candles trending `down` or `up`, then the given
    (open, high, low, close) candles."""
    sign = -1 if trend == "down" else 1
    rows, price = [], start
    for _ in range(lead_in):
        o = price
        c = price + sign * step
        rows.append((o, max(o, c) + 0.3, min(o, c) - 0.3, c))
        price = c
    shift = price - pattern[0][0]             # continue from where the lead-in ended
    rows += [tuple(v + shift for v in candle) for candle in pattern]
    return frame(rows)
