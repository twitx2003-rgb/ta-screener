"""Opportunities (tascreen/opportunities.py): made-up stocks and prices."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd

from tascreen import opportunities
from tascreen.config import FlowSettings

CFG = FlowSettings()
DAY = date(2026, 9, 4)


def _bars(closes: list[float] | np.ndarray) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"timestamp": pd.bdate_range(end=pd.Timestamp(DAY), periods=len(c)), "open": c,
                         "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1_000_000.0})


def _pullback(top: float = 100.0) -> pd.DataFrame:
    """A long rise, then a dip back to just over its rising 20-day average."""
    rise = list(np.linspace(top / 2, top, 200))
    return _bars(rise + [top * k for k in (0.995, 0.99, 0.988, 0.985, 0.99)])


def _stretched() -> pd.DataFrame:
    """A long rise, then a run far above the averages."""
    rise = np.linspace(50.0, 100.0, 200)
    return _bars(list(rise) + [100.0 * 1.025 ** k for k in range(1, 16)])


def _falling() -> pd.DataFrame:
    return _bars(np.linspace(100.0, 50.0, 200))


def _snap(**stocks) -> dict:
    return {"day": DAY.isoformat(), "passing": sorted(stocks), "stocks": stocks}


def test_a_stock_back_on_its_rising_average_is_an_opportunity():
    got = opportunities.of(_pullback(), CFG)
    assert got and got["n"] == 20 and got["close"] >= got["average"] and 0 <= got["over_atr"] <= CFG.opp_max_over_20_atr


def test_a_stretched_or_falling_stock_is_not():
    assert opportunities.of(_stretched(), CFG) is None
    assert opportunities.of(_falling(), CFG) is None
    assert opportunities.of(_pullback().tail(30), CFG) is None                    # too short to judge


def test_a_stock_that_never_came_back_to_its_average_is_not():
    riding = _bars(np.linspace(60.0, 100.0, 200))
    assert opportunities.of(riding, replace(CFG, opp_touch_atr=0.0)) is None
    assert opportunities.of(riding, CFG) is not None


def test_the_highest_rating_comes_first_and_only_the_passing_stocks_count():
    stocks = {"NASDAQ:LOW": {"ticker": "LOW", "rs": 85, "industry": "Packaged Software"},
              "NASDAQ:TOP": {"ticker": "TOP", "rs": 97, "industry": "Packaged Software"},
              "NASDAQ:RUN": {"ticker": "RUN", "rs": 99, "industry": "Packaged Software"}}
    snap = _snap(**stocks)
    bars = {"NASDAQ:LOW": _pullback(), "NASDAQ:TOP": _pullback(), "NASDAQ:RUN": _stretched()}
    found = opportunities.find(snap, bars.get, CFG, break_atr=0.5)
    assert [o["ticker"] for o in found] == ["TOP", "LOW"]
    assert found[0]["stop"] < found[0]["average"] and found[0]["risk_pct"] > 0
    snap["passing"] = ["NASDAQ:LOW"]
    assert [o["ticker"] for o in opportunities.find(snap, bars.get, CFG, break_atr=0.5)] == ["LOW"]


def test_an_exit_too_far_under_the_close_is_no_opportunity():
    snap, bars = _snap(**{"NASDAQ:AAA": {"rs": 90}}), {"NASDAQ:AAA": _pullback()}
    assert opportunities.find(snap, bars.get, CFG, break_atr=0.5)
    assert not opportunities.find(snap, bars.get, replace(CFG, opp_max_risk_pct=0.5), break_atr=0.5)


def test_a_night_takes_new_ones_with_variety():
    found = [{"symbol": f"N:{k}", "industry": i} for k, i in enumerate("aaabbc")]
    got = opportunities.choose(found, skip={"N:0"}, per_night=3, per_industry=2)
    assert [o["symbol"] for o in got] == ["N:1", "N:2", "N:3"]
    got = opportunities.choose(found, skip=set(), per_night=8, per_industry=1)
    assert [o["symbol"] for o in got] == ["N:0", "N:3", "N:5"]


def _one(**more) -> dict:
    return {"symbol": "NASDAQ:AAA", "ticker": "AAA", "industry": "Packaged Software", "sector": "Technology Services",
            "rs": 93, "from_high_pct": -7.4, "n": 20, "average": 95.0, "atr": 2.0, "close": 96.0, "over_atr": 0.5,
            "stop": 94.0, "risk_pct": 2.1, **more}


def test_the_message_is_in_hebrew_and_says_the_list_only_when_true():
    text = opportunities.message(DAY, [_one()], listed=True)
    assert "AAA" in text and "ממוצע 20" in text and "94.00" in text and "2.1%" in text and "לא ייעוץ השקעות" in text
    assert "Packaged Software" not in text and "טריידינגוויו" in text
    assert "טריידינגוויו" not in opportunities.message(DAY, [_one()], listed=False)
    assert len(opportunities.caption(_one())) < 1024
    assert "ליד השיא" in opportunities.what(_one(from_high_pct=-0.2))


def test_the_chart_draws_the_averages_and_the_exit():
    bars = _pullback()
    found = opportunities.find(_snap(**{"NASDAQ:AAA": {"ticker": "AAA", "rs": 90}}), {"NASDAQ:AAA": bars}.get, CFG,
                               break_atr=0.5)
    svg = opportunities.chart(bars, found[0])
    assert "AAA" in svg and "ממוצע 20 יום" in svg and "ממוצע 50 יום" in svg and "יציאה" in svg
    assert opportunities.photos(found, {"NASDAQ:AAA": bars}.get, lambda s: b"png")[0][0] == b"png"

    def broken(_svg: str) -> bytes:
        raise RuntimeError("no browser")

    assert opportunities.photos(found, {"NASDAQ:AAA": bars}.get, broken) == []     # the list goes out anyway


def test_one_on_the_list_leaves_when_it_closes_under_its_average():
    from tascreen import setups_list

    record = {"NASDAQ:AAA": {"n": 20, "average": 95.0, "atr": 2.0, "stop": 94.0, "rs": 93}}
    got = setups_list.sent_setups(DAY.isoformat(), picks=[], live={}, leaders=[], allowed=None, opps=record)
    entry = {"symbol": "NASDAQ:AAA", **got["NASDAQ:AAA"], "since": "2026-09-03"}
    assert entry["kind"] == "opp" and entry["level"] == "ma" and entry["n"] == 20 and entry["watch"] == 95.0
    assert setups_list.KIND_HE["opp"] and setups_list.rank(entry)
    bars = _pullback()
    fell = bars.copy()
    fell.loc[fell.index[-1], "close"] = 60.0

    def view(frame):
        return SimpleNamespace(stocks=pd.DataFrame({"symbol": ["NASDAQ:AAA"], "close": [float(frame["close"].iloc[-1])],
                                                    "last_date": [DAY.isoformat()]}), day=DAY)

    kept, events = setups_list.review([entry], view(bars), lambda s: bars, break_atr=0.5, keep_sessions=10)
    assert len(kept) == 1 and not events
    kept, events = setups_list.review([entry], view(fell), lambda s: fell, break_atr=0.5, keep_sessions=10)
    assert not kept and events[0]["reason"] == "fell_ma"


# ------------------------------------------------- the evening's run
class _Bot:
    def __init__(self, fail: bool = False):
        self.fail, self.texts, self.albums = fail, [], []

    def send(self, text, html=False):
        if self.fail:
            from tascreen.errors import ScreenerError
            raise ScreenerError("telegram down")
        self.texts.append(text)

    def send_album(self, photos):
        self.albums.append(photos)


def _evening(tmp_path, monkeypatch, bot, *, list_fails: bool = False):
    import run
    from tascreen import alerts, flow, notify, setups_list
    from tascreen.store import Store

    monkeypatch.setattr(notify, "from_environment", lambda: bot)
    monkeypatch.setattr(alerts, "default_png", lambda svg: b"png")
    settings, store = run.load_settings(), Store(tmp_path)
    if not settings.setups_list.id:
        import pytest
        pytest.skip("no list id in config.yaml")
    calls = []

    class Session:
        async def call_tool(self, name, args):
            from fakes import result
            if list_fails:
                raise OSError("down")
            calls.append((name, args))
            return result({"success": True})

    class Client:
        @asynccontextmanager
        async def own_list_session(self, list_id, name):
            yield Session()

    bars = {f"NASDAQ:S{k}": _pullback() for k in range(4)}
    store.read_bars = bars.get
    stocks = {s: {"ticker": s.split(":")[1], "rs": 90 + k, "industry": f"Industry {k}"} for k, s in enumerate(bars)}
    flow.save(tmp_path, _snap(**stocks))
    setups_list.write(tmp_path, {"id": settings.setups_list.id, "version": 4, "entries": [], "managed": []})
    return run, settings, store, Client(), calls


def test_the_evening_sends_them_once_and_puts_them_on_the_list(tmp_path, monkeypatch):
    from tascreen import alerts, setups_list

    bot = _Bot()
    run, settings, store, client, calls = _evening(tmp_path, monkeypatch, bot)
    assert run._opportunities(settings, store, DAY, client) == {"status": "waiting for the evening report"}
    alerts.write_sent(store, DAY, {"evening": {"picks": 0}})
    got = run._opportunities(settings, store, DAY, client)
    assert got == {"found": 4, "sent": 4, "listed": 4}
    assert len(bot.texts) == 1 and "טריידינגוויו" in bot.texts[0] and len(bot.albums[0]) == 4
    assert calls[0][0] == setups_list.ADD and len(calls[0][1]["symbols"]) == 4
    entries = setups_list.read(tmp_path)["entries"]
    assert {e["kind"] for e in entries} == {"opp"} and len(entries) == 4
    assert run._opportunities(settings, store, DAY, client)["status"] == "already sent"
    assert len(bot.texts) == 1


def test_the_night_after_does_not_send_them_again(tmp_path, monkeypatch):
    from tascreen import alerts, flow

    bot = _Bot()
    run, settings, store, client, _ = _evening(tmp_path, monkeypatch, bot)
    alerts.write_sent(store, date(2026, 9, 3), {"opportunities": {"NASDAQ:S3": {"n": 20}}})
    alerts.write_sent(store, DAY, {"evening": {"picks": 1}, "live": {"NASDAQ:S2|flag": {"line": 1.0}}})
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "picks.json").write_text(
        f'[{{"day": "{DAY.isoformat()}", "symbol": "NASDAQ:S1", "pattern": "flag"}}]', encoding="utf-8")
    got = run._opportunities(settings, store, DAY, client)
    assert got["found"] == 4 and got["sent"] == 1                          # S3 sent lately; S2, S1 sent tonight
    assert "S0" in bot.texts[0] and "S3" not in bot.texts[0]
    assert flow.read(tmp_path, DAY.isoformat())["day"] == DAY.isoformat()


def test_a_list_failure_still_sends_without_claiming_the_list(tmp_path, monkeypatch):
    from tascreen import alerts

    bot = _Bot()
    run, settings, store, client, _ = _evening(tmp_path, monkeypatch, bot, list_fails=True)
    alerts.write_sent(store, DAY, {"evening": {"picks": 0}})
    got = run._opportunities(settings, store, DAY, client)
    assert got["sent"] == 4 and got["listed"] == 0
    assert "טריידינגוויו" not in bot.texts[0]
    later = run._sent_recommendations(settings, store, DAY, None)          # the night's refresh takes them in
    assert len(later) == 4 and {v["kind"] for v in later.values()} == {"opp"}


def test_a_telegram_failure_is_tried_again(tmp_path, monkeypatch):
    from tascreen import alerts

    bot = _Bot(fail=True)
    run, settings, store, client, _ = _evening(tmp_path, monkeypatch, bot)
    alerts.write_sent(store, DAY, {"evening": {"picks": 0}})
    assert run._opportunities(settings, store, DAY, client)["status"] == "failed"
    assert "opportunities" not in alerts.read_sent(store, DAY)
    bot.fail = False
    assert run._opportunities(settings, store, DAY, client)["sent"] == 4
