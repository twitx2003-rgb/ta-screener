from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from synth import frame
from tascreen.indicators import atr, cross_check, ema, latest, rsi, sma


def test_sma_and_ema_by_hand():
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert sma(s, 3).tolist()[2:] == [2.0, 3.0, 4.0]
    assert math.isnan(sma(s, 3).iloc[1])
    e = ema(s, 3)                                  # alpha 0.5, seeded with the first value
    assert math.isnan(e.iloc[1]) and e.iloc[2] == pytest.approx(2.25)


def test_rsi_extremes_and_balance():
    up = pd.Series(np.arange(1.0, 60.0))
    assert rsi(up).iloc[-1] == 100.0
    zigzag = pd.Series([100 + (i % 2) for i in range(200)], dtype=float)
    assert rsi(zigzag).iloc[-1] == pytest.approx(50, abs=2)
    assert rsi(up).iloc[:14].isna().all()


def test_atr_of_constant_ranges():
    rows = [(10.0, 11.0, 9.0, 10.0)] * 40
    assert atr(frame(rows), 14).iloc[-1] == pytest.approx(2.0)


def test_latest_reports_crosses_and_52_week_distance():
    closes = [100.0] * 220 + [100 + 2 * i for i in range(1, 21)]          # a late rally
    rows = [(c, c + 1, c - 1, c) for c in closes]
    values = latest(frame(rows))
    assert values["above_sma50"] is True and values["above_sma150"] is True
    assert values["pct_from_52w_high"] == pytest.approx((140 / 141 - 1) * 100)
    assert values["golden_cross_days_ago"] >= 0
    assert values["bars"] == 240


def test_short_history_gives_nan_not_a_guess():
    rows = [(10.0, 11.0, 9.0, 10.0)] * 30
    values = latest(frame(rows))
    assert math.isnan(values["sma150"]) and values["above_sma150"] is None


def test_the_long_average_is_150_sessions():
    closes = [float(i) for i in range(1, 181)]
    values = latest(frame([(c, c + 1, c - 1, c) for c in closes]))
    assert values["sma150"] == pytest.approx(sum(closes[-150:]) / 150)
    assert "sma200" not in values and "above_sma200" not in values


def test_golden_cross_is_sma50_over_sma150():
    # 190 bars: too few for a 200-day average, enough for 150
    closes = [100.0 - 0.1 * i for i in range(170)] + [83.0 + 3 * i for i in range(1, 21)]
    values = latest(frame([(c, c + 1, c - 1, c) for c in closes]))
    assert 0 <= values["golden_cross_days_ago"] < 20
    assert math.isnan(values["death_cross_days_ago"])


def test_cross_check_counts_agreement():
    df = pd.DataFrame({"symbol": ["A", "B"], "rsi14": [50.0, 70.0], "tv_rsi": [50.4, 60.0],
                       "ema50": [100.0, 100.0], "tv_ema50": [100.1, 110.0],
                       "ema200": [math.nan, 90.0], "tv_ema200": [80.0, 90.0]})
    report = cross_check(df)
    assert report["rsi14"]["agree"] == 1 and report["rsi14"]["compared"] == 2
    assert report["ema50"]["agree"] == 1
    assert report["ema200"]["compared"] == 1 and report["ema200"]["agree"] == 1
