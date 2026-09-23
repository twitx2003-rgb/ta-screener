from __future__ import annotations

import asyncio
import math

import pytest

from fakes import FakeScreener, stock_row
from tascreen.config import UniverseSettings
from tascreen.errors import ProviderError
from tascreen.tv.data import RateLimited
from tascreen.universe import fetch_universe

B = 1e9


def _cfg(**kw):
    return UniverseSettings(**{"band_edges": (1e9, 2e9, 5e9), "row_cap": 10, **kw})


def _run(screener, cfg=None, delays=()):
    return asyncio.run(fetch_universe(screener, cfg or _cfg(), delays=delays))


def _rows(n, start=1.01, step=0.37, exchange="NASDAQ"):
    return [stock_row(i, (start + step * i) * B, exchange) for i in range(n)]


def test_bands_over_the_cap_are_split_until_everything_is_fetched():
    rows = _rows(60)                          # 1.01B .. ~22.8B, many per band
    frame, summary = _run(FakeScreener(rows, cap=10))
    assert len(frame) == 60 == summary["total_including_dropped"]
    assert summary["kept"] == 60
    assert all(b["rows"] <= 10 for b in summary["bands"])
    assert frame["market_cap"].is_monotonic_decreasing
    assert frame["symbol"].iloc[-1] == "NASDAQ:S0000"


def test_an_answer_over_the_size_limit_splits_its_band_too():
    rows = _rows(60)
    screener = FakeScreener(rows, cap=10, too_big_over=4)   # under the row cap, still too big
    frame, summary = _run(screener)
    assert len(frame) == 60
    assert all(b["rows"] <= 4 for b in summary["bands"])


def test_other_tool_errors_are_not_mistaken_for_size():
    from tascreen.tv.data import ToolFailed

    class Broken(FakeScreener):
        async def call_tool(self, name, args):
            from types import SimpleNamespace
            if args["limit"] > 1:
                return SimpleNamespace(is_error=True, structured_content=None,
                                       content=[SimpleNamespace(text="internal error")])
            return await super().call_tool(name, args)

    with pytest.raises(ToolFailed, match="internal error"):
        _run(Broken(_rows(3)))


def test_otc_is_counted_for_completeness_then_dropped():
    rows = _rows(8) + [stock_row(100 + i, (3 + i) * B, "OTC") for i in range(3)]
    frame, summary = _run(FakeScreener(rows))
    assert summary["total_including_dropped"] == 11
    assert summary["dropped"] == {"OTC": 3} and len(frame) == 8
    assert not frame["exchange"].eq("OTC").any()


def test_preferred_issues_are_dropped_after_counting():
    rows = _rows(4) + [stock_row(50, 4 * B, "NYSE", subtype="preferred"),
                       stock_row(51, 4.5 * B, "OTC", subtype="preferred")]
    frame, summary = _run(FakeScreener(rows))
    assert len(frame) == 4
    assert summary["dropped"] == {"OTC": 1, "subtype preferred": 1}
    assert summary["subtypes"] == {"common": 4}


def test_null_values_are_kept_as_gaps_not_invented():
    rows = [stock_row(1, 3 * B, RSI=None, close=None, sector=None)]
    frame, _ = _run(FakeScreener(rows))
    assert math.isnan(frame["tv_rsi"].iloc[0]) and math.isnan(frame["close"].iloc[0])
    assert frame["sector"].iloc[0] is None


def test_row_outside_its_band_means_the_filter_is_not_what_we_assume():
    class Ignores(FakeScreener):
        async def call_tool(self, name, args):
            args = {**args, "filters": {"market_cap_basic": [None, None]}}
            return await super().call_tool(name, args)

    with pytest.raises(ProviderError, match="outside the band"):
        _run(Ignores(_rows(5)))


def test_band_short_of_its_total_under_the_cap_is_refused():
    class Short(FakeScreener):
        async def call_tool(self, name, args):
            answer = await super().call_tool(name, args)
            data = answer.structured_content["data"]
            if args["limit"] > 1 and len(data["rows"]) > 1:
                data["rows"] = data["rows"][:-1]          # one row silently missing
            return answer

    with pytest.raises(ProviderError, match="under the 10-row cap"):
        _run(Short(_rows(5)))


def test_a_stock_crossing_a_band_edge_mid_fetch_is_retried_once():
    rows = _rows(6)

    class Moving(FakeScreener):
        count_calls = 0

        async def call_tool(self, name, args):
            if args["limit"] == 1:
                self.count_calls += 1
                if self.count_calls == 1:            # the total moved during the first pass
                    answer = await super().call_tool(name, args)
                    answer.structured_content["data"]["totalCount"] += 1
                    return answer
            return await super().call_tool(name, args)

    frame, _ = _run(Moving(rows))
    assert len(frame) == 6


def test_a_persistent_count_mismatch_fails():
    class Wrong(FakeScreener):
        async def call_tool(self, name, args):
            answer = await super().call_tool(name, args)
            if args["limit"] == 1:
                answer.structured_content["data"]["totalCount"] += 1
            return answer

    with pytest.raises(ProviderError, match="twice"):
        _run(Wrong(_rows(4)))


@pytest.mark.parametrize("change,match", [
    ({"symbol": "NVDA"}, "EXCHANGE:TICKER"),
    ({"type": "fund"}, "stocks only"),
])
def test_bad_rows_are_refused(change, match):
    with pytest.raises(ProviderError, match=match):
        _run(FakeScreener([stock_row(1, 3 * B, **change)]))


def test_missing_key_names_what_is_there():
    row = stock_row(1, 3 * B)
    row["_filter_cap"] = row.pop("market_cap_basic")
    with pytest.raises(ProviderError, match="Actual keys"):
        _run(FakeScreener([row]))


def test_rate_limit_is_retried_then_raised():
    screener = FakeScreener(_rows(3))
    screener.rate_limited = 1
    frame, _ = _run(screener, delays=(0,))
    assert len(frame) == 3
    screener.rate_limited = 5
    with pytest.raises(RateLimited):
        _run(screener, delays=(0,))


def test_band_edges_must_start_at_the_floor():
    from tascreen.errors import ConfigError

    with pytest.raises(ConfigError):
        UniverseSettings(band_edges=(2e9, 5e9))
