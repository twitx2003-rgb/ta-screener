"""Formatting for the pages, and the check that no page shows a raw nan/None.

A missing value is always shown as an em dash. Numbers go inside `num` spans
(left to right) in the templates; in Hebrew prose signed numbers are avoided,
because RTL layout moves a leading minus sign to the wrong end.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Any

import pandas as pd

MISSING = "—"


def ok(x: Any) -> bool:
    """True for a finite number (bools count as missing: they are not amounts)."""
    if x is None or isinstance(x, bool):
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def num(x: Any, digits: int = 2) -> str:
    return f"{float(x):,.{digits}f}" if ok(x) else MISSING


def price(x: Any) -> str:
    """Stock prices: 2 decimals, 4 under $1."""
    if not ok(x):
        return MISSING
    return f"{float(x):,.{4 if abs(float(x)) < 1 else 2}f}"


def pct(x: Any, digits: int = 1, signed: bool = True) -> str:
    if not ok(x):
        return MISSING
    return f"{float(x):+,.{digits}f}%" if signed else f"{float(x):,.{digits}f}%"


def money(x: Any) -> str:
    """USD amounts such as market cap: $1.23T, $45.6B, $789M."""
    if not ok(x):
        return MISSING
    value = float(x)
    for size, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            scaled = value / size
            return f"${scaled:,.{2 if abs(scaled) < 10 else 1}f}{unit}"
    return f"${value:,.0f}"


def day(x: Any) -> str:
    """A date as DD/MM/YYYY (the Israeli order)."""
    if x is None or x is pd.NaT or (isinstance(x, float) and math.isnan(x)):
        return MISSING
    if isinstance(x, str):
        try:
            x = date.fromisoformat(x[:10])
        except ValueError:
            return MISSING
    if isinstance(x, pd.Timestamp):
        if pd.isna(x):
            return MISSING
        x = x.date()
    if isinstance(x, datetime):
        x = x.date()
    if not isinstance(x, date):
        return MISSING
    return x.strftime("%d/%m/%Y")


def sign_class(x: Any) -> str:
    if not ok(x) or float(x) == 0:
        return ""
    return "up" if float(x) > 0 else "down"


def clean(obj: Any) -> Any:
    """JSON-ready copy: NaN/inf/NaT -> None, numpy scalars -> Python, dates -> ISO."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if obj is None or obj is pd.NaT:
        return None
    if isinstance(obj, (pd.Timestamp, date, datetime)):
        return obj.isoformat()
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()                      # numpy scalar -> Python
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


def script_json(obj: Any) -> str:
    """JSON for a <script type="application/json"> block: no NaN, no '</'."""
    return json.dumps(clean(obj), ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).replace("</", "<\\/")


# ------------------------------------------------------------------ page check
_BAD_WORD = re.compile(r"(?<![\w.\-])(nan|NaN|None|NaT|inf|-inf|Infinity|undefined|null)(?![\w.])")
_BAD_ATTR = re.compile(r"=\"(nan|NaN|None|NaT|inf|undefined)\"")


def page_problems(html: str) -> list[str]:
    """Raw nan/None/NaT leaking into a page (text, attributes or embedded JSON)."""
    problems = []
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S)
    for body in scripts:
        if re.search(r"\bNaN\b|\bInfinity\b", body):
            problems.append("embedded JSON holds NaN/Infinity")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    problems += [f"attribute value '{m}'" for m in _BAD_ATTR.findall(text)]
    text = re.sub(r"<[^>]+>", " ", text)
    problems += [f"text shows '{m}'" for m in _BAD_WORD.findall(text)]
    return problems
