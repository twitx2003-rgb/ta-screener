"""Why the market moves: the index funds, the news it reads, its rules, and when a session
move deserves an explanation. Synthetic values only."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from tascreen import explain, premarket
from tascreen.llm import SyntheticLLM

NOW = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


def index(close=100.0, change=0.2, pre_close=99.0, pre_change=-1.0, pre_abs=-1.0):
    return {"close": close, "change": change, "premarket_close": pre_close, "premarket_change": pre_change,
            "premarket_change_abs": pre_abs}


def test_the_index_funds_are_read_and_stale_pre_market_values_skipped():
    class Result:
        def __init__(self, payload):
            self.is_error, self.structured_content, self.content = False, payload, []

    class Session:
        async def call_tool(self, tool, arguments):
            assert tool == "mcp-tv-get-symbol-data-batch" and "AMEX:SPY" in arguments["symbols"]
            return Result({"data": {"AMEX:SPY": index(), "NASDAQ:QQQ": index(pre_close=90.0)},
                           "missing": [], "success": True})

    got = asyncio.run(explain.fetch_indexes(Session(), delays=()))
    assert set(got) == {"AMEX:SPY", "NASDAQ:QQQ"}
    assert got["AMEX:SPY"]["premarket_fresh"] and not got["NASDAQ:QQQ"]["premarket_fresh"]
    assert explain.moves(got, premarket=True) == {"SPY": -1.0}                # QQQ's is yesterday's
    assert explain.index_line(got, premarket=False) == "📈 SPY +0.2% · QQQ +0.2%"


def test_the_news_comes_from_the_desks_last_hours():
    stamp = NOW.timestamp()
    state = {"sent_recent": [{"at": stamp - 600, "author": "A", "summary_he": "חדשה"},
                             {"at": stamp - 5 * 3600, "author": "B", "summary_he": "ישנה"}],
             "recent": [{"added": stamp - 300, "author": "C", "created_at": "t", "text": "CPI came in hot"}]}
    asked = []
    news = explain.recent_news(NOW, token="k", get=lambda url, h: asked.append(url) or json.dumps(state).encode())
    assert news == {"sent": [{"author": "A", "summary_he": "חדשה", "minutes_ago": 10}],
                    "posts": [{"author": "C", "time": "t", "text": "CPI came in hot"}]}
    assert "ref=xnews" in asked[0]
    assert explain.recent_news(NOW, token="") == {"sent": [], "posts": []}


def _explainer(text, cause=True):
    return SyntheticLLM(lambda system, user, schema: {"explanation_he": text, "cause_found": cause})


def test_the_explanation_keeps_to_the_input():
    indexes = {"AMEX:SPY": {**index(pre_change=-1.2, pre_abs=-1.0), "premarket_fresh": True}}
    news = {"sent": [], "posts": [{"author": "C", "time": "t", "text": "CPI 3.4% vs 3.1% expected"}]}
    good = explain.explain(_explainer("ה-S&P 500 יורד 1.2% אחרי שמדד המחירים עלה ל-3.4%."),
                           moment="pre-market", indexes=indexes, movers=[], news=news)
    assert good and "3.4%" in good
    invented = explain.explain(_explainer("ה-S&P 500 יורד 1.2% כי האבטלה עלתה ל-4.7%."),
                               moment="pre-market", indexes=indexes, movers=[], news=news)
    advice = explain.explain(_explainer("כדאי למכור עכשיו."), moment="pre-market", indexes=indexes,
                             movers=[], news=news)
    assert invented is None and advice is None


def test_a_sharp_session_move_is_explained_at_most_so_often():
    kw = dict(day_pct=1.0, hour_pct=0.7, max_per_day=3, min_gap_minutes=45)
    calm = {"AMEX:SPY": index(close=100.0, change=0.4), "NASDAQ:QQQ": index(close=100.0, change=0.5)}
    down = {"AMEX:SPY": index(close=98.8, change=-1.2), "NASDAQ:QQQ": index(close=98.5, change=-1.5)}
    assert explain.move_due(NOW, calm, [], [], **kw) is None
    assert explain.move_due(NOW, down, [], [], **kw) == "day"
    done = [{"at": NOW.isoformat(), "moves": {"SPY": -1.2, "QQQ": -1.5}}]
    assert explain.move_due(NOW + timedelta(minutes=20), down, [], done, **kw) is None       # too soon
    assert explain.move_due(NOW + timedelta(minutes=60), down, [], done, **kw) is None       # no further
    deeper = {"AMEX:SPY": index(close=97.5, change=-2.0), "NASDAQ:QQQ": index(close=97.0, change=-2.3)}
    assert explain.move_due(NOW + timedelta(minutes=60), deeper, [], done, **kw) == "day"
    hour_ago = [(NOW - timedelta(minutes=60), {"SPY": 100.0, "QQQ": 100.0})]
    quick = {"AMEX:SPY": index(close=99.2, change=-0.6), "NASDAQ:QQQ": index(close=99.6, change=-0.2)}
    assert explain.move_due(NOW, quick, hour_ago, [], **kw) == "hour"
    assert explain.move_due(NOW, down, [], done * 3, **kw) is None                           # daily cap
    assert explain.move_message(down, None).endswith("לא מצאתי בחדשות של השעות האחרונות הסבר ברור לתנועה.")


def test_the_pre_market_report_carries_the_why():
    text = premarket.message("08:30", {"up": [], "down": [], "stale": 0}, [], 4,
                             index_line="📈 SPY -1.0%", why="השוק יורד <בגלל> הנתון.")
    assert "📈 SPY -1.0%" in text and "🧭 <b>למה:</b> השוק יורד &lt;בגלל&gt; הנתון." in text
    assert "אין מניות שזזות" in text
