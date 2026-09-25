"""The ledger replayed as trades (made-up prices): entry, exits, splits, statistics."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tascreen import backtest as bt


def _bars(opens, closes, start="2026-01-05"):
    days = pd.bdate_range(start, periods=len(opens))
    return pd.DataFrame({"timestamp": days, "open": opens, "close": closes,
                         "high": [max(o, c) + 1 for o, c in zip(opens, closes)],
                         "low": [min(o, c) - 1 for o, c in zip(opens, closes)]})


def _row(symbol, outcome, breakout, resolved, *, direction="bullish", target=110.0, scale=1.0,
         pattern="double_bottom"):
    return {"symbol": symbol, "pattern": pattern, "direction": direction, "source": "backfill",
            "outcome": outcome, "breakout_day": breakout, "resolved_day": resolved,
            "target": target, "scale": scale}


BARS = _bars(opens=[100, 101, 102, 104, 106, 108, 109, 111],
             closes=[100, 102, 103, 105, 107, 109, 110, 112])       # 5..14 Jan 2026


def test_entries_exits_and_what_is_skipped():
    ledger = pd.DataFrame([
        _row("A", "target", "2026-01-05", "2026-01-13"),              # enter 101, exit at 110
        _row("B", "failed", "2026-01-06", "2026-01-08"),               # enter 102, exit close 105
        _row("C", "target", "2026-01-05", "2026-01-05"),               # resolved before the entry
        _row("D", "target", "2026-01-12", "2026-01-14", target=105.0), # entry 109 already past 105
        _row("E", "failed", "2026-01-05", "2026-01-07", direction="bearish", target=90.0),
        _row("F", "open", "2026-01-05", None)])                         # not decided
    done = bt.trades(ledger, lambda s: BARS)
    by = {r.symbol: r for r in done.itertuples()}
    assert set(by) == {"A", "B", "E"}
    assert by["A"].entry == 101 and by["A"].exit == 110 and by["A"].return_pct == pytest.approx(8.911, abs=1e-3)
    assert by["A"].entry_day == date(2026, 1, 6) and by["A"].sessions == 6
    assert by["B"].exit == 105 and by["B"].return_pct == pytest.approx(2.941, abs=1e-3)
    assert by["E"].return_pct == pytest.approx(-1.980, abs=1e-3)          # short: 101 -> 103
    assert by["A"].mae_pct == pytest.approx(0.990, abs=1e-3)              # the low 100 vs entry 101


def test_a_split_after_the_breakout_scales_the_target():
    ledger = pd.DataFrame([_row("S", "target", "2026-01-05", "2026-01-13", target=220.0, scale=0.5)])
    done = bt.trades(ledger, lambda s: BARS)
    assert done.iloc[0]["exit"] == 110.0                                  # 220 before a 2:1 split


def test_the_statistics_per_pattern_and_in_total():
    frame = pd.DataFrame({"pattern": ["p"] * 4 + ["q"], "direction": ["bullish"] * 5,
                          "outcome": ["target", "failed", "target", "expired", "failed"],
                          "entry_day": [date(2026, 1, d) for d in (5, 6, 7, 8, 9)],
                          "exit_day": [date(2026, 2, d) for d in (1, 2, 3, 4, 5)],
                          "return_pct": [10.0, -5.0, 6.0, -1.0, -2.0], "sessions": [10, 4, 8, 60, 3],
                          "mae_pct": [1.0, 5.0, 2.0, 3.0, 4.0]})
    rows = bt.summary(frame, min_trades=3)
    total, p = rows[0], rows[1]
    assert (total["pattern"], total["trades"], len(rows)) == ("all", 5, 2)      # q: too few
    assert p["win_pct"] == 50.0 and p["avg_return_pct"] == 2.5 and p["profit_factor"] == pytest.approx(2.67, abs=0.01)
    assert p["target_pct"] == 50.0 and p["median_mae_pct"] == 2.5 and p["losing_streak"] == 1
    assert total["losing_streak"] == 2                                     # -1 then -2 at the end
    assert bt.summary(pd.DataFrame(columns=frame.columns), 3) == []


def test_the_scorecard_page_shows_the_backtest(tmp_path):
    from fastapi.testclient import TestClient

    from tascreen.config import Settings
    from tascreen.store import Store
    from tascreen.web import fmt
    from tascreen.web.app import HOST, create_app

    settings = Settings(root=tmp_path)
    store = Store(settings.data_dir)
    store.write_backtest({"trades": 1234, "period": {"from": "2020-01-02", "to": "2026-03-20"},
                          "rows": [{"pattern": "all", "direction": "bullish", "trades": 1234,
                                    "win_pct": 51.0, "avg_return_pct": 2.5, "median_return_pct": 1.0,
                                    "profit_factor": 1.4, "avg_sessions": 30.0, "median_mae_pct": 8.0,
                                    "p90_mae_pct": 20.0}]})
    page = TestClient(create_app(settings), base_url=f"http://{HOST}").get("/scorecard")
    assert page.status_code == 200 and fmt.page_problems(page.text) == []
    assert "בדיקה לאחור" in page.text and "1,234" in page.text and "כל התבניות" in page.text
