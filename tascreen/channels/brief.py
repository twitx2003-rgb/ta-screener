"""The facts an agent may use for one topic, keyed so that posts can cite them.

Keys look like `IBM.rsi14`, `IBM.pattern.target`, `IBM.check.min_rise_between_pct`,
`IBM.live.price`. A fact is {value, label, unit}: the value is what a post may
quote (numbers rounded the way the site shows them), the label says in Hebrew
what it is. Missing values (NaN, None) are left out, never guessed.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..web import labels


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _day(value: Any) -> str | None:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10] if value else None


class _Facts:
    def __init__(self, ticker: str):
        self.ticker, self.items = ticker, {}

    def number(self, key: str, value: Any, label: str, unit: str = "", digits: int = 2) -> None:
        if _finite(value):
            self.items[f"{self.ticker}.{key}"] = {"value": round(float(value), digits),
                                                  "label": label, "unit": unit}

    def text(self, key: str, value: Any, label: str) -> None:
        if value is not None and value == value and str(value).strip():
            self.items[f"{self.ticker}.{key}"] = {"value": str(value), "label": label, "unit": ""}

    def flag(self, key: str, value: Any, label: str) -> None:
        if isinstance(value, bool) or (hasattr(value, "item") and isinstance(value.item(), bool)):
            self.items[f"{self.ticker}.{key}"] = {"value": "כן" if bool(value) else "לא",
                                                  "label": label, "unit": ""}


def topic_brief(stock: dict[str, Any], det: dict[str, Any], topic: str,
                live: dict[str, Any] | None = None) -> dict[str, Any]:
    """Facts about one stock and one of its detections (and its live crossing, if any)."""
    f = _Facts(stock["ticker"])
    f.text("symbol", stock["symbol"], "סימול")
    f.text("name", stock.get("description"), "שם החברה")
    f.text("sector", labels.sector(stock.get("sector")), "סקטור")
    f.number("market_cap_billion", (stock.get("market_cap") or math.nan) / 1e9, "שווי שוק", "מיליארד $", 1)
    f.text("last_date", stock.get("last_date"), "יום המסחר של הסגירה האחרונה")
    f.number("close", stock.get("close"), "סגירה אחרונה", "$")
    f.number("change_1d_pct", stock.get("change_1d_pct"), "שינוי ביום האחרון", "%", 1)
    f.number("change_20d_pct", stock.get("change_20d_pct"), "שינוי ב-20 ימי מסחר", "%", 1)
    f.number("rsi14", stock.get("rsi14"), "RSI 14", "", 1)
    f.number("sma50", stock.get("sma50"), "ממוצע נע 50 יום", "$")
    f.number("sma150", stock.get("sma150"), "ממוצע נע 150 יום", "$")
    f.flag("above_sma50", stock.get("above_sma50"), "המחיר מעל ממוצע 50")
    f.flag("above_sma150", stock.get("above_sma150"), "המחיר מעל ממוצע 150")
    f.number("atr_pct", stock.get("atr_pct"), "ATR כאחוז מהמחיר (תנודתיות יומית)", "%", 1)
    f.number("rel_volume", stock.get("rel_volume"), "נפח יחסי (היום מול ממוצע 50 יום)", "x", 2)
    f.number("pct_from_52w_high", stock.get("pct_from_52w_high"), "מרחק מהשיא של 52 שבועות", "%", 1)
    f.number("pct_from_52w_low", stock.get("pct_from_52w_low"), "מרחק מהשפל של 52 שבועות", "%", 1)
    f.number("golden_cross_days_ago", stock.get("golden_cross_days_ago"),
             "ימי מסחר מאז חציית זהב (50 מעל 150)", "", 0)
    f.number("death_cross_days_ago", stock.get("death_cross_days_ago"),
             "ימי מסחר מאז חציית מוות (50 מתחת ל-150)", "", 0)

    f.text("pattern.name", det.get("name_he"), "התבנית")
    f.text("pattern.family", labels.FAMILY.get(det.get("family"), det.get("family")), "סוג")
    f.text("pattern.status", labels.STATUS.get(det.get("status"), det.get("status")), "מצב")
    f.text("pattern.direction", labels.DIRECTION.get(det.get("direction"), det.get("direction")), "כיוון")
    f.text("pattern.start", _day(det.get("start")), "תחילת התבנית")
    f.text("pattern.end", _day(det.get("end")), "סוף התבנית (נקודת המפנה האחרונה)")
    f.text("pattern.breakout_date", _day(det.get("breakout_date")), "יום הפריצה")
    f.number("pattern.breakout_price", det.get("breakout_price"), "מחיר הפריצה", "$")
    f.number("pattern.height", det.get("height"), "גובה התבנית", "$")
    f.number("pattern.target", det.get("target"), "יעד לפי כלל המדידה של הספר (לא תחזית)", "$")
    if det.get("status") == "forming":
        f.number("pattern.trigger_up", det.get("trigger_up"), "פריצה אם תהיה סגירה מעל", "$")
        f.number("pattern.trigger_down", det.get("trigger_down"), "פריצה אם תהיה סגירה מתחת ל", "$")
    trend = labels.VOLUME_TREND.get(det.get("volume_trend") or "", "")
    if trend and trend != "—":
        f.text("pattern.volume_trend", trend, "מגמת הנפח בתוך התבנית")
    checks = det.get("checks")
    if checks is None and det.get("checks_json"):
        checks = json.loads(det["checks_json"])
    seen: dict[str, int] = {}
    for c in checks or []:
        rule = c["rule"]
        seen[rule] = seen.get(rule, 0) + 1
        key = f"check.{rule}" + (f"_{seen[rule]}" if seen[rule] > 1 else "")
        verdict = "עבר" if c.get("passed") else ("טרם" if c.get("value") is None else "לא עבר")
        label = f"{c['label_he']} (סף {labels.check_text(c.get('threshold'))}; {verdict})"
        if _finite(c.get("value")):
            f.number(key, c["value"], label, "", 2)
        else:
            f.text(key, labels.check_text(c.get("value")), label)
    if live:
        f.number("live.price", live.get("price"), "מחיר חי (באיחור של 15 דקות לפחות)", "$")
        f.number("live.change_pct", live.get("change_pct"), "שינוי היום לפי המחיר החי", "%", 1)
        f.number("live.crossing_level", live.get("level"),
                 "קו הפריצה שהמחיר החי חצה (לא סופי עד הסגירה)", "$")
        f.text("live.crossing_direction", "מעלה" if live.get("direction") == "bullish" else "מטה",
               "כיוון החצייה")
    return {"topic": topic, "symbol": stock["symbol"], "ticker": stock["ticker"],
            "pattern": det.get("pattern"), "facts": f.items}


def recap_brief(channel_list, detections: pd.DataFrame) -> dict[str, Any]:
    """The day's counts for the #כללי recap."""
    facts: dict[str, dict] = {"scan.detections": {"value": int(len(detections)),
                                                  "label": "זיהויים בסריקה", "unit": ""}}
    for status, n in detections["status"].value_counts().items():
        facts[f"scan.status.{status}"] = {"value": int(n), "label": f"זיהויים במצב {labels.STATUS.get(status, status)}",
                                          "unit": ""}
    for ch in channel_list:
        if ch.id != "general":
            facts[f"scan.count.{ch.id}"] = {"value": ch.count, "label": f"זיהויים של {ch.title}", "unit": ""}
    return {"topic": "recap", "symbol": None, "ticker": None, "pattern": None, "facts": facts}
