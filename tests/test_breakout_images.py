"""Every bullish breakout with a chart of its pattern (made-up prices): the charts, their
captions, the albums, and the text that still goes out when a chart cannot be drawn."""
from __future__ import annotations

import json
import xml.dom.minidom
from datetime import datetime, timezone

import pytest

from tascreen import alerts
from tascreen.notify import Telegram


def _scan(tmp_path):
    from test_web_live import RULES, _setup
    from tascreen.market_hours import next_sessions
    from tascreen.web.data import ScanRepository

    settings, store, day = _setup(tmp_path)     # NASDAQ:DB: a forming double bottom
    return store, ScanRepository(store, RULES).current(), next_sessions(day, 1)[0]


def _svg_bytes(svg: str) -> bytes:
    xml.dom.minidom.parseString(svg)            # a well-formed chart
    return svg.encode("utf-8")


def test_a_live_crossing_gets_its_forming_pattern_and_the_live_price(tmp_path):
    from test_web_live import LIVE_PRICE

    store, view, session = _scan(tmp_path)
    found = alerts.live_crossings(view, {"NASDAQ:DB": LIVE_PRICE}, session, {})
    at = datetime(2026, 3, 20, 15, 40, tzinfo=timezone.utc)
    photos = alerts.crossing_photos(found, store.read_bars, at, "America/New_York", "https://s.example",
                                    to_png=_svg_bytes)
    assert len(photos) == 1
    svg, caption = photos[0][0].decode("utf-8"), photos[0][1]
    assert f"{LIVE_PRICE:,.2f}" in svg and "חי" in svg                   # the live price is marked
    assert caption.startswith("<b>⚡ פריצה תוך כדי מסחר") and "DB</a>" in caption
    assert "לא סופי עד הסגירה" in caption and len(caption) <= 1024


def test_a_confirmed_breakout_chart_and_its_report_line(tmp_path):
    store, view, session = _scan(tmp_path)
    row = view.detections.loc[view.detections["symbol"] == "NASDAQ:DB"].iloc[0].to_dict()
    record = alerts.detection_record(row)
    breakout = {"symbol": "NASDAQ:DB", "pattern": row["pattern"], "name": row["name_he"],
                "breakout": 112.3, "target": 124.9, "invalidation": 100.0, "close": 113.0,
                "rel_volume": 1.8, "hit_rate": 55.0, "record": record}
    long_news = {"title": "x" * 900, "hours": 2.0, "provider": "Reuters",
                 "link": "https://www.tradingview.com/news/a/", "published": None}
    photos, missing = alerts.breakout_photos([breakout], store.read_bars, "https://s.example",
                                             {"NASDAQ:DB": long_news}, to_png=_svg_bytes)
    assert missing == 0 and len(photos) == 1
    caption = photos[0][1]
    assert caption.startswith('1. <a href="https://s.example/symbol/NASDAQ_DB/">DB</a>')
    assert len(caption) <= 1000 and "📰" in caption and "..." in caption   # a long title is cut
    assert "יעד 124.90 (כלל המדידה)" in caption
    short = alerts.breakout_block(1, breakout, "https://s.example", long_news, limit=250)
    assert len(short) <= 250 and "📰" not in short and "פריצה 112.30" in short   # the news gave way


def test_no_bars_or_a_failed_drawing_means_text_only(tmp_path):
    store, view, session = _scan(tmp_path)

    def broken(svg):
        raise OSError("rsvg-convert crashed")

    item = {"symbol": "NASDAQ:DB", "pattern": "p", "name": "n", "breakout": 1.0, "target": 2.0,
            "invalidation": 0.5, "close": 1.1, "rel_volume": 1.0, "hit_rate": None,
            "record": {"symbol": "NASDAQ:DB"}}
    assert alerts.breakout_photos([item], lambda s: None, "https://s.example")[1] == 1
    assert alerts.breakout_photos([item], store.read_bars, "https://s.example", to_png=broken)[1] == 1


def test_charts_go_in_albums_of_ten():
    uploads = []
    bot = Telegram("123456:" + "x" * 30, 42,
                   upload=lambda m, p, f: uploads.append((m, p, f)) or {"ok": True})
    bot.send_album([(b"png%d" % n, f"<b>{n}</b>") for n in range(21)])
    assert [u[0] for u in uploads] == ["sendMediaGroup", "sendMediaGroup", "sendPhoto"]
    media = json.loads(uploads[0][1]["media"])
    assert len(media) == 10 and media[0] == {"type": "photo", "media": "attach://p0",
                                             "caption": "<b>0</b>", "parse_mode": "HTML"}
    assert [f[0] for f in uploads[0][2]] == [f"p{n}" for n in range(10)]
    assert uploads[2][1]["parse_mode"] == "HTML" and uploads[2][2][0] == "photo"


def test_several_files_in_one_multipart_body():
    captured = {}
    bot = Telegram("123456:" + "x" * 30, 42)
    bot._request = lambda method, data, headers, timeout: captured.update(data=data) or {"ok": True}
    bot.send_album([(b"AAA", "a"), (b"BBB", "b")])
    body = captured["data"]
    assert b'name="p0"; filename="p0.png"' in body and b'name="p1"; filename="p1.png"' in body
    assert body.index(b"AAA") < body.index(b"BBB") and body.rstrip().endswith(b"--")


def test_the_evening_report_sends_the_charts_after_the_text(tmp_path):
    from test_alerts import DAY, _view

    class Bot:
        def __init__(self):
            self.events = []

        def send(self, text, html=False):
            self.events.append("text")

        def send_album(self, photos):
            self.events.append(("album", len(photos)))

    store, bot = alerts.Store(tmp_path), Bot()
    result = alerts.evening_report(store, _view(), alerts.AlertsSettings(top_analyses=0), bot=bot,
                                   min_cases=20, dispatch=lambda w, i: 204, can_dispatch=False,
                                   images=True, to_png=_svg_bytes)
    # the made-up view's stocks have no stored bars: no chart, and the text still went out
    assert bot.events[0] == "text" and result["charts"] == 0 and result["charts_missing"] == 2
