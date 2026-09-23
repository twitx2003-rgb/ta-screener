"""US trading days and session completeness.

A daily bar fetched while the exchange is still open is not a close: its "close"
is the last trade so far and its volume covers part of the day. A pattern that
"breaks out" on such a bar may not exist by the close, so today's bar is dropped
until the session has ended. TradingView's `get-ohlcv` does return the live
session's bar (seen in market-research-pipeline, 2026-09).

The holiday calendar is rule-based, plus a short list of past unscheduled
closures (a national day of mourning, a storm). Future ones cannot be known in
advance; a bar check that finds a whole-market missing day should add it here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

EARLY_CLOSE = time(13, 0)


@dataclass(frozen=True)
class DroppedBar:
    date: str
    close: float
    volume: float
    market_time: str   # wall-clock time at the exchange when we decided

    def as_dict(self) -> dict:
        return {"date": self.date, "close": self.close, "volume": self.volume,
                "market_time": self.market_time}


def drop_incomplete_session(
    df: pd.DataFrame,
    *,
    market_tz: str,
    session_close: str,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, DroppedBar | None]:
    """Remove today's bar if the exchange has not closed yet.

    Bar dates are the UTC calendar date of `timestamp` (TradingView stamps the
    session open, 13:30 or 14:30 UTC, which is the same calendar day).
    `session_close` sits a little after the official close so the closing
    auction has printed; on half-days the bar is held back until then too — a
    few hours of extra staleness, never a partial bar.
    """
    if df.empty:
        return df, None

    tz = ZoneInfo(market_tz)
    now_local = (now or datetime.now(tz)).astimezone(tz)
    close_h, close_m = (int(part) for part in session_close.split(":"))

    last = df.iloc[-1]
    last_date = pd.Timestamp(last["timestamp"]).tz_convert("UTC").date()

    if last_date != now_local.date() or now_local.time() >= time(close_h, close_m):
        return df, None

    dropped = DroppedBar(
        date=last_date.isoformat(),
        close=float(last["close"]),
        volume=float(last["volume"]),
        market_time=now_local.strftime("%Y-%m-%d %H:%M %Z"),
    )
    return df.iloc[:-1].reset_index(drop=True), dropped


def us_early_close(day: date) -> time | None:
    """13:00 on the three regular US half-days, else None: the day after
    Thanksgiving, and July 3 / December 24 when they fall Monday-Thursday (on a
    Friday they are the observed holiday and the market is closed)."""
    if day.month == 11 and day.weekday() == 4:
        first_thursday = 1 + (3 - date(day.year, 11, 1).weekday()) % 7
        if day.day == first_thursday + 22:          # fourth Thursday + 1
            return EARLY_CLOSE
    if (day.month, day.day) in ((7, 3), (12, 24)) and day.weekday() <= 3:
        return EARLY_CLOSE
    return None


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(year, month, (h + l - 7 * m + 114) % 31 + 1)


def _observed(day: date) -> date:
    """Saturday holidays move to Friday, Sunday ones to Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def us_market_holidays(year: int) -> set[date]:
    """Full-day NYSE/Nasdaq closures by rule."""
    days = {
        _nth_weekday(year, 1, 0, 3),                 # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),                 # Washington's Birthday
        _easter(year) - timedelta(days=2),           # Good Friday
        _last_weekday(year, 5, 0),                   # Memorial Day
        _observed(date(year, 7, 4)),                 # Independence Day
        _nth_weekday(year, 9, 0, 1),                 # Labor Day
        _nth_weekday(year, 11, 3, 4),                # Thanksgiving
        _observed(date(year, 12, 25)),               # Christmas
    }
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:                      # a Saturday New Year is not moved back
        days.add(_observed(new_year))
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))       # Juneteenth
    return days


# Closures no rule can produce. Found by comparing live bars with the calendar:
# every symbol lacked 2025-01-09 (national day of mourning for President Carter).
UNSCHEDULED_CLOSURES = frozenset({
    date(2012, 10, 29), date(2012, 10, 30),     # Hurricane Sandy
    date(2018, 12, 5),                          # mourning, President G. H. W. Bush
    date(2025, 1, 9),                           # mourning, President Carter
})


def is_trading_day(day: date) -> bool:
    return (day.weekday() < 5 and day not in us_market_holidays(day.year)
            and day not in UNSCHEDULED_CLOSURES)


def sessions_between(after: date, until: date) -> int:
    """Scheduled trading days d with after < d <= until."""
    count, day = 0, after
    while day < until:
        day += timedelta(days=1)
        if is_trading_day(day):
            count += 1
    return count


def last_completed_session(now: datetime, *, market_tz: str, session_close: str) -> date:
    """The newest trading day whose bar is a close at `now` (see drop_incomplete_session)."""
    local = now.astimezone(ZoneInfo(market_tz))
    close_h, close_m = (int(part) for part in session_close.split(":"))
    day = local.date()
    if is_trading_day(day) and local.time() >= time(close_h, close_m):
        return day
    day -= timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def next_sessions(after: date, count: int) -> list[date]:
    """The next `count` scheduled trading days after `after`."""
    out, day = [], after
    while len(out) < count:
        day += timedelta(days=1)
        if is_trading_day(day):
            out.append(day)
    return out
