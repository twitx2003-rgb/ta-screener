from __future__ import annotations

from datetime import date, datetime, time, timezone

from tascreen.market_hours import (is_trading_day, last_completed_session, next_sessions,
                                   sessions_between, us_early_close, us_market_holidays)


def test_last_completed_session():
    def at(y, m, d, hh, mm):
        return last_completed_session(datetime(y, m, d, hh, mm, tzinfo=timezone.utc),
                                      market_tz="America/New_York", session_close="16:15")

    assert at(2026, 9, 23, 20, 30) == date(2026, 9, 23)    # 16:30 New York: done
    assert at(2026, 9, 23, 20, 0) == date(2026, 9, 22)     # 16:00 New York: not yet
    assert at(2026, 9, 26, 12, 0) == date(2026, 9, 25)     # Saturday -> Friday
    assert at(2026, 9, 8, 12, 0) == date(2026, 9, 4)       # Tuesday morning after Labor Day


def test_2026_holidays_by_rule():
    days = us_market_holidays(2026)
    assert date(2026, 4, 3) in days          # Good Friday
    assert date(2026, 6, 19) in days         # Juneteenth (a Friday)
    assert date(2026, 7, 3) in days          # July 4 is a Saturday -> observed Friday
    assert date(2026, 11, 26) in days        # Thanksgiving
    assert date(2026, 12, 25) in days


def test_unscheduled_closures_are_not_trading_days():
    assert not is_trading_day(date(2025, 1, 9))      # every live symbol lacked this day
    assert is_trading_day(date(2025, 1, 10))


def test_saturday_new_year_is_not_moved_back():
    # 2022-01-01 was a Saturday; NYSE stayed open on 2021-12-31.
    assert date(2021, 12, 31) not in us_market_holidays(2021)
    assert date(2021, 12, 31) not in us_market_holidays(2022)


def test_half_days():
    assert us_early_close(date(2026, 11, 27)) == time(13, 0)    # day after Thanksgiving
    assert us_early_close(date(2026, 12, 24)) == time(13, 0)    # a Thursday
    assert us_early_close(date(2026, 7, 3)) is None             # a Friday: holiday, not half-day
    assert us_early_close(date(2026, 9, 22)) is None


def test_counting_sessions_skips_weekends_and_holidays():
    assert not is_trading_day(date(2026, 9, 7))                 # Labor Day
    # Fri 2026-09-04 -> Tue 2026-09-08: Monday is Labor Day, so one session.
    assert sessions_between(date(2026, 9, 4), date(2026, 9, 8)) == 1
    assert sessions_between(date(2026, 9, 8), date(2026, 9, 8)) == 0
    assert next_sessions(date(2026, 9, 4), 2) == [date(2026, 9, 8), date(2026, 9, 9)]
