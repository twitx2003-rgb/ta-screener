"""Breakout alerts: the evening report's lists, ranking, messages and send-once rule
(made-up stocks and prices)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pandas as pd

from tascreen import github
from tascreen.alerts import (bullish_breakouts, evening_messages, evening_report, hit_rates,
                             on_the_verge, read_sent)
from tascreen.config import AlertsSettings
from tascreen.store import Store
from tascreen.web.data import ScanView

DAY = date(2026, 3, 20)
POINTS = json.dumps([{"date": "2026-02-02", "price": 90.0}, {"date": "2026-02-20", "price": 100.0},
                     {"date": "2026-03-06", "price": 91.0}])


def _det(symbol, pattern, status, direction, *, day=DAY, breakout=100.0, target=110.0, trigger=None):
    return {"symbol": symbol, "family": "chart", "pattern": pattern, "name_he": f"שם {pattern}",
            "status": status, "direction": direction, "event_day": day, "start": pd.Timestamp("2026-02-02"),
            "end": pd.Timestamp(day), "breakout_price": breakout, "target": target,
            "trigger_up": trigger if trigger is not None else float("nan"),
            "points_json": POINTS, "lines_json": "[]", "checks_json": "[]"}


def _view() -> ScanView:
    stocks = pd.DataFrame([
        {"symbol": "NYSE:AAA", "close": 101.0, "rel_volume": 1.2, "last_date": "2026-03-20"},
        {"symbol": "NYSE:BBB", "close": 102.0, "rel_volume": 2.5, "last_date": "2026-03-20"},
        {"symbol": "NYSE:CCC", "close": 99.0, "rel_volume": 0.8, "last_date": "2026-03-20"},
        {"symbol": "NYSE:DDD", "close": 50.0, "rel_volume": 1.0, "last_date": "2026-03-20"},
        {"symbol": "NYSE:EEE", "close": 50.0, "rel_volume": 1.0, "last_date": "2026-03-19"}])
    detections = pd.DataFrame([
        _det("NYSE:AAA", "double_bottom", "breakout", "bullish"),
        _det("NYSE:BBB", "ascending_triangle", "breakout", "bullish"),
        _det("NYSE:CCC", "double_top", "breakout", "bearish"),                    # bearish: out
        _det("NYSE:AAA", "rectangle", "breakout", "bullish", day=date(2026, 3, 19)),  # yesterday
        _det("NYSE:DDD", "rectangle", "forming", "either", trigger=50.9),          # 1.8% below
        _det("NYSE:DDD", "flag", "forming", "bullish", trigger=52.0),              # 4% below
        _det("NYSE:EEE", "rectangle", "forming", "bullish", trigger=50.5)])        # stale bar
    return ScanView(DAY, {"symbols_scanned": 5, "symbols_in_universe": 6}, stocks, detections)


def test_the_days_bullish_breakouts_strongest_first_and_the_verge():
    rates = {"double_bottom": 55.0}
    found = bullish_breakouts(_view(), lambda s: None, rates)
    assert [b["symbol"] for b in found] == ["NYSE:BBB", "NYSE:AAA"]      # volume 2.5 before 1.2
    assert found[1]["hit_rate"] == 55.0 and found[1]["invalidation"] == 90.0
    verge = on_the_verge(_view(), 2.0)
    assert [(v["symbol"], v["pattern"]) for v in verge] == [("NYSE:DDD", "rectangle")]
    assert round(verge[0]["gap_pct"], 1) == 1.8


def test_hit_rates_need_enough_decided_cases_and_a_target():
    ledger = pd.DataFrame({"pattern": ["a"] * 4 + ["b"] * 4 + ["c"] * 2,
                           "outcome": ["target", "failed", "target", "open"] * 2 + ["target"] * 2,
                           "target": [1.0] * 4 + [float("nan")] * 4 + [1.0] * 2})
    assert hit_rates(ledger, 3) == {"a": 66.7}              # b has no target, c too few cases


def test_the_messages_are_escaped_linked_and_fit_telegram():
    breakouts = bullish_breakouts(_view(), lambda s: None, {})
    breakouts[0]["name"] = "<b>x</b>"
    verge = on_the_verge(_view(), 2.0) * 80                  # long: split into messages
    messages = evening_messages(DAY, breakouts, verge, site_url="https://site.example", verge_pct=2.0,
                                coverage=(5, 6), analyses=["NYSE:BBB"])
    assert all(len(m) <= 4096 for m in messages) and len(messages) >= 1
    text = "\n".join(messages)
    assert "&lt;b&gt;x&lt;/b&gt;" in text and '<a href="https://site.example/symbol/NYSE_BBB/">BBB</a>' in text
    assert "(נסרקו 5 מתוך 6 מניות)" in text and "ניתוח מלא יגיע" in text and "ועוד 50 באתר" in text
    assert "לא ייעוץ השקעות" in messages[-1] and "(כלל המדידה)" in text
    empty = "\n".join(evening_messages(DAY, [], [], site_url="https://s.example", verge_pct=2.0))
    assert "אין היום פריצות שוריות" in empty and "מעקב המסחר" not in empty
    watched = "\n".join(evening_messages(DAY, [], [], site_url="https://s.example", verge_pct=2.0,
                                         live_summary={"passes": 78, "watched": 60, "alerts": 3,
                                                       "data_delay_min": 15.5}))
    assert "78 סבבים · 60 מניות במעקב · 3 התראות · עיכוב הנתונים כ-15.5 דק'" in watched


class _Bot:
    def __init__(self):
        self.sent = []

    def send(self, text, html=False):
        self.sent.append(text)


def test_the_report_goes_once_and_starts_the_strongest_analyses(tmp_path):
    store, bot, started = Store(tmp_path), _Bot(), []

    def dispatch(workflow, inputs):
        started.append((workflow, inputs))
        return 204 if inputs["symbol"] == "NYSE:BBB" else 403

    cfg = AlertsSettings(top_analyses=2)
    now = lambda: datetime(2026, 3, 20, 22, tzinfo=timezone.utc)
    first = evening_report(store, _view(), cfg, bot=bot, min_cases=20, dispatch=dispatch,
                           can_dispatch=True, now=now)
    assert first == {"status": "sent", "breakouts": 2, "verge": 1, "analyses": 1, "messages": 1,
                     "intraday_held": 0, "intraday_fell": 0, "charts": 0, "charts_missing": 0}
    assert started == [("analyst.yml", {"symbol": "NYSE:BBB"}), ("analyst.yml", {"symbol": "NYSE:AAA"})]
    assert "AAA" in bot.sent[-1] and "לא הצלחתי" in bot.sent[-1]     # the refused one is told
    assert read_sent(store, DAY)["evening"]["analyses"] == ["NYSE:BBB"]
    again = evening_report(store, _view(), cfg, bot=bot, min_cases=20, dispatch=dispatch,
                           can_dispatch=True, now=now)
    assert again == {"status": "already sent"} and len(started) == 2


def test_the_report_says_which_intraday_breakouts_held_at_the_close(tmp_path):
    from tascreen.alerts import write_sent

    store, bot = Store(tmp_path), _Bot()
    write_sent(store, DAY, {"live": {"NYSE:DDD|rectangle": {"line": 49.0, "price": 49.5},
                                     "NYSE:AAA|double_bottom": {"line": 105.0, "price": 105.4}}})
    result = evening_report(store, _view(), AlertsSettings(top_analyses=0), bot=bot, min_cases=20,
                            dispatch=lambda w, i: 204, can_dispatch=False)
    assert (result["intraday_held"], result["intraday_fell"]) == (1, 1)
    text = "\n".join(bot.sent)
    assert "✅ החזיקה מעל הקו" in text and "DDD</a>" in text        # closed 50.0 above 49.0
    assert "❌ חזרה מתחת לקו" in text and "AAA</a>" in text         # closed 101.0 below 105.0
    assert "live" in read_sent(store, DAY)                         # kept beside the evening entry


def test_the_near_stocks_every_pass_and_the_delay_measure():
    from tascreen.alerts import bar_age_minutes, watch_tiers

    view = _view()
    assert watch_tiers(view, 2.0, 5.0, 10) == (["NYSE:DDD"], [])          # 1.8% away: near
    assert watch_tiers(view, 1.0, 5.0, 10) == ([], ["NYSE:DDD"])
    now = datetime(2026, 3, 20, 15, 0, tzinfo=timezone.utc)
    bar = {"bars": [{"t": int(datetime(2026, 3, 20, 14, 44, tzinfo=timezone.utc).timestamp())}]}
    assert bar_age_minutes(bar, now) == 16.0 and bar_age_minutes({"bars": []}, now) is None


def test_dispatch_posts_the_workflow_and_its_inputs():
    calls = []
    status = github.dispatch("analyst.yml", {"symbol": "NYSE:BBB"}, token=" key\n",
                             post=lambda url, body, headers: calls.append((url, body, headers)) or 204)
    url, body, headers = calls[0]
    assert status == 204 and url.endswith("/repos/twitx2003-rgb/ta-screener/actions/workflows/analyst.yml/dispatches")
    assert json.loads(body) == {"ref": "main", "inputs": {"symbol": "NYSE:BBB"}}
    assert headers["Authorization"] == "Bearer key"
    assert github.dispatch("x.yml", {}, token="", post=lambda *a: 204) == 0      # no key, no call


# ------------------------------------------------------------------ during the session
def _live_setup(tmp_path):
    from test_web_live import RULES, _setup
    from tascreen.config import AlertsSettings, LiveSettings, Settings
    from tascreen.market_hours import next_sessions
    from tascreen.web.data import ScanRepository

    settings, store, day = _setup(tmp_path)     # a real scan: NASDAQ:DB's double bottom is forming
    settings = Settings(root=tmp_path, alerts=AlertsSettings(watch_pct=10.0),
                        live=LiveSettings(after_close_minutes=0))
    view = ScanRepository(store, RULES).current()
    return settings, store, view, next_sessions(day, 1)[0]


def _ohlcv(session_day, price, days_back=0):
    from datetime import datetime as dt, timedelta, timezone
    from zoneinfo import ZoneInfo

    opened = dt.combine(session_day - timedelta(days=days_back), dt.min.time(),
                        tzinfo=ZoneInfo("America/New_York")).replace(hour=9, minute=30)
    return {"success": True, "bars": [{"t": int(opened.astimezone(timezone.utc).timestamp()),
                                       "o": price, "h": price, "l": price, "c": price, "v": 1000}]}


def test_the_watch_list_and_a_crossing_told_once(tmp_path):
    from tascreen.alerts import live_crossings, live_message, live_price, watch_list
    from test_web_live import LIVE_PRICE

    settings, store, view, session = _live_setup(tmp_path)
    assert watch_list(view, 10.0, 5) == ["NASDAQ:DB"] and watch_list(view, 1.0, 5) == []
    assert live_price(_ohlcv(session, 113.0), session, "America/New_York") == 113.0
    assert live_price(_ohlcv(session, 113.0, days_back=1), session, "America/New_York") is None
    found = live_crossings(view, {"NASDAQ:DB": LIVE_PRICE}, session, {})
    assert [(c["symbol"], c["pattern"]) for c in found] == [("NASDAQ:DB", "double_bottom")]
    assert live_crossings(view, {"NASDAQ:DB": LIVE_PRICE}, session, {found[0]["key"]: {}}) == []
    assert live_crossings(view, {"NASDAQ:DB": 105.0}, session, {}) == []      # still below the line
    text = live_message(found, datetime(2026, 3, 20, 15, 40, tzinfo=timezone.utc),
                        "America/New_York", "https://site.example")
    assert "לא סופי עד הסגירה" in text and "11:40 שעון ניו יורק" in text and "DB</a>" in text


def test_ci_live_prices_the_watch_list_and_alerts_once(tmp_path, monkeypatch):
    import asyncio

    import run
    import tascreen.market_hours as hours
    import tascreen.notify
    from tascreen.alerts import read_sent
    from test_web_live import LIVE_PRICE

    settings, store, view, session = _live_setup(tmp_path)
    calls, sent = [], []

    class Result:
        def __init__(self, payload):
            self.is_error, self.structured_content, self.content = False, payload, []

    class Session:
        async def call_tool(self, tool, arguments):
            calls.append((tool, arguments["symbol"]))
            return Result(_ohlcv(session, LIVE_PRICE))

    class Client:
        def with_session(self, work):
            return asyncio.run(work(Session()))

    class Bot:
        def send(self, text, html=False):
            sent.append(text)

    def bounds(day, tz):
        now = datetime.now(timezone.utc)
        return now, now + pd.Timedelta(seconds=0.3)

    monkeypatch.setattr(run, "make_tradingview", lambda s: Client())
    monkeypatch.setattr(tascreen.notify, "from_environment", lambda: Bot())
    monkeypatch.setattr(hours, "live_session", lambda now, **kw: session)
    monkeypatch.setattr(hours, "session_bounds", bounds)
    assert run.ci_live(settings, 345) == 0
    summary = json.loads((settings.log_dir / "live_summary.json").read_text(encoding="utf-8"))
    assert summary["ended"] == "the session is over" and summary["watched"] == 1
    assert summary["alerts"] == 1 and summary["calls"] >= 1 and "NASDAQ" not in json.dumps(summary)
    assert calls[0] == ("mcp-tv-get-ohlcv", "NASDAQ:DB") and len(sent) == 2
    assert "המעקב במהלך המסחר התחיל" in sent[0] and "1 מניות במעקב" in sent[0]   # once a session
    assert "פריצה תוך כדי מסחר" in sent[1]
    assert "NASDAQ:DB|double_bottom" in read_sent(store, session)["live"]
    assert run.ci_live(settings, 345) == 0 and len(sent) == 2               # neither told twice


def test_ci_live_ends_at_once_when_the_market_is_closed(tmp_path, monkeypatch):
    import run
    import tascreen.market_hours as hours
    import tascreen.notify

    settings, store, view, session = _live_setup(tmp_path)
    monkeypatch.setattr(tascreen.notify, "from_environment", lambda: object())
    monkeypatch.setattr(hours, "live_session", lambda now, **kw: None)
    monkeypatch.setattr(hours, "next_open", lambda now, **kw: now + pd.Timedelta(hours=10))
    assert run.ci_live(settings, 345) == 0
    summary = json.loads((settings.log_dir / "live_summary.json").read_text(encoding="utf-8"))
    assert summary["ended"] == "the market is closed" and summary["passes"] == 0


# ------------------------------------------------------------------ news
def _news_payload(now):
    def row(title, hours, symbols, link="https://www.tradingview.com/news/x/"):
        return {"id": title, "title": title, "published": int(now.timestamp() - hours * 3600),
                "link": link, "provider": {"id": "p", "name": "Dow Jones Newswires"}, "urgency": 2,
                "relatedSymbols": [{"symbol": s} for s in symbols], "paywall": True}
    return {"success": True, "data": {"count": 4, "has_more": False, "offset": 0, "total_available": 4,
            "headlines": [row("Old news <b>", 60, ["NYSE:AAA"]),
                          row("Market wrap names everyone", 1, ["NYSE:AAA", "B:B", "C:C", "D:D"]),
                          row("AAA wins a big contract <script>", 5, ["NYSE:AAA"]),
                          row("AAA older story", 20, ["NYSE:AAA"])]}}


def test_the_headline_is_the_newest_about_the_stock_itself():
    from tascreen.alerts import news_line, pick_headline

    now = datetime(2026, 3, 20, 21, 0, tzinfo=timezone.utc)
    best = pick_headline(_news_payload(now), "NYSE:AAA", now)
    assert best["title"] == "AAA wins a big contract <script>" and round(best["hours"]) == 5
    line = news_line(best)
    assert line.startswith("📰 <a href=\"https://www.tradingview.com/news/x/\">AAA wins a big contract &lt;script&gt;</a>")
    assert "(Dow Jones Newswires, לפני 5 שעות)" in line
    assert pick_headline(_news_payload(now), "NYSE:ZZZ", now) is None and news_line(None) == ""
    foreign = {**best, "link": "https://evil.example/x"}
    assert "<a" not in news_line(foreign)                          # only TradingView's own links


def test_the_news_goes_under_each_breakout_and_live_crossing():
    from tascreen.alerts import live_message

    now = datetime(2026, 3, 20, 21, 0, tzinfo=timezone.utc)
    news = {"NYSE:BBB": {"title": "BBB beats estimates", "hours": 2.0, "provider": "Reuters",
                         "link": "https://www.tradingview.com/news/y/", "published": now}}
    breakouts = bullish_breakouts(_view(), lambda s: None, {})
    text = "\n".join(evening_messages(DAY, breakouts, [], site_url="https://s.example", verge_pct=2.0,
                                      news=news))
    assert "BBB beats estimates" in text and text.count("📰") == 1
    found = [{"symbol": "NYSE:BBB", "name": "משולש", "line": 100.0, "price": 101.0, "target": 110.0,
              "key": "k"}] * 60
    message = live_message(found, now, "America/New_York", "https://s.example", news)
    assert len(message) <= 4096 and "BBB beats estimates" in message and "באתר." in message
