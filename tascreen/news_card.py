"""A picture for an X news story that came without one (owner, 2026-10-10: "where possible
a picture from the post, and if there is none, make one yourself").

The card is the morning digest's design (tascreen/digest_render.py: a light page, the Heebo
font, drawn by the browser): a strip with how urgent the story is, the account and the time;
the story's sentence large; why it matters below it. A story that names a stock with stored
bars also gets that stock's last three months as candles, with its last close and the
change over the period (the bars end at the last nightly update, and the card says so).
No emoji in the picture: the runner has no emoji font.
"""
from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Any

import pandas as pd

from .digest_render import FONT

WIDTH, HEIGHT = 1080, 608
CHART_SESSIONS = 63               # about three months of daily candles
KINDS = {5: ("מבזק", "#dc2626"), 4: ("חשוב", "#ea580c")}
REGULAR = ("עדכון", "#1d4ed8")
UP, DOWN = "#059669", "#dc2626"

CSS = """
@font-face { font-family: Heebo; src: url("%(font)s"); font-weight: 100 900; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: %(w)dpx; height: %(h)dpx; overflow: hidden; }
body { background: #eef1f7; font-family: Heebo, "Segoe UI", Arial, sans-serif; color: #0f172a;
       padding: 28px; direction: rtl; }
.card { height: 100%%; background: #ffffff; border-radius: 22px; overflow: hidden; display: flex;
        flex-direction: column; box-shadow: 0 1px 2px rgba(15,23,42,.06), 0 6px 18px rgba(15,23,42,.06);
        border-right: 12px solid %(colour)s; }
.top { display: flex; align-items: center; gap: 14px; padding: 26px 34px 0; height: 74px; }
.kind { font-size: 22px; font-weight: 800; color: #ffffff; background: %(colour)s; border-radius: 999px;
        padding: 4px 18px; }
.who { font-size: 24px; font-weight: 700; color: #334155; direction: ltr; unicode-bidi: isolate; }
.when { margin-right: auto; font-size: 22px; color: #64748b; direction: ltr; unicode-bidi: isolate; }
.main { flex: 1; display: flex; gap: 26px; padding: 6px 34px 0; min-height: 0; align-items: center; }
.text { flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center; }
.headline { font-weight: 800; line-height: 1.22; font-size: %(size)dpx; color: #0f172a;
            display: -webkit-box; -webkit-line-clamp: %(lines)d; -webkit-box-orient: vertical; overflow: hidden; }
.why { margin-top: 18px; font-size: 25px; line-height: 1.4; color: #475569; font-weight: 500;
       display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.why b { color: %(colour)s; font-weight: 700; }
.chart { flex: 0 0 400px; background: #f6f8fc; border-radius: 18px; padding: 16px 18px 10px;
         display: flex; flex-direction: column; }
.chart .head { display: flex; align-items: baseline; gap: 12px; direction: ltr; }
.chart .sym { font-size: 30px; font-weight: 800; color: #0f172a; }
.chart .close { font-size: 24px; font-weight: 700; color: #334155; }
.chart .move { font-size: 22px; font-weight: 800; margin-left: auto; }
.chart .note { font-size: 17px; color: #64748b; margin-top: 2px; }
.foot { height: 52px; display: flex; align-items: center; justify-content: space-between;
        padding: 0 34px; font-size: 18px; color: #94a3b8; }
"""


def _e(text: Any) -> str:
    return html.escape(str(text or ""))


def _size(headline: str, chart: bool) -> tuple[int, int]:
    """(font size, lines): a short sentence big, a long one smaller; smaller beside a chart."""
    n = len(headline)
    size, lines = (62, 3) if n <= 70 else (52, 4) if n <= 120 else (44, 5)
    return (size - 14, lines + 1) if chart else (size, lines)


def stock_chart(bars: pd.DataFrame, symbol: str) -> dict[str, Any] | None:
    """The card's chart data from a stock's stored daily bars (None for too few)."""
    tail = bars.tail(CHART_SESSIONS).reset_index(drop=True)
    if len(tail) < 20:
        return None
    first, last = float(tail["close"].iloc[0]), float(tail["close"].iloc[-1])
    return {"symbol": symbol.split(":")[-1], "rows": tail[["open", "high", "low", "close"]].to_numpy(float).tolist(),
            "close": last, "change_pct": (last / first - 1) * 100 if first else math.nan,
            "last_day": pd.Timestamp(tail["timestamp"].iloc[-1]).strftime("%d/%m")}


def _candles(rows: list[list[float]], width: int = 364, height: int = 250) -> str:
    lo = min(r[2] for r in rows)
    hi = max(r[1] for r in rows)
    span = (hi - lo) or 1.0
    step = width / len(rows)
    body = max(1.5, step * 0.62)
    y = lambda price: 6 + (hi - price) / span * (height - 12)        # noqa: E731
    out = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
    for n, (o, h, low, c) in enumerate(rows):
        x = step * n + step / 2
        colour = UP if c >= o else DOWN
        top, bottom = y(max(o, c)), y(min(o, c))
        out.append(f'<line x1="{x:.1f}" y1="{y(h):.1f}" x2="{x:.1f}" y2="{y(low):.1f}" stroke="{colour}" stroke-width="1.4"/>')
        out.append(f'<rect x="{x - body / 2:.1f}" y="{top:.1f}" width="{body:.1f}" height="{max(1.2, bottom - top):.1f}" '
                   f'fill="{colour}" rx="0.6"/>')
    out.append("</svg>")
    return "".join(out)


def render_html(*, headline: str, why: str, author: str, importance: int, when: str,
                chart: dict[str, Any] | None = None) -> tuple[str, int, int]:
    """(the page, its width, its height) for one story."""
    label, colour = KINDS.get(int(importance), REGULAR)
    size, lines = _size(headline, bool(chart))
    css = CSS % {"font": FONT.resolve().as_uri(), "w": WIDTH, "h": HEIGHT, "colour": colour,
                 "size": size, "lines": lines}
    out = [f'<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><style>{css}</style></head>'
           '<body><div class="card">',
           f'<div class="top"><span class="kind">{_e(label)}</span><span class="who">@{_e(author)}</span>'
           f'<span class="when">{_e(when)}</span></div>',
           '<div class="main"><div class="text">',
           f'<div class="headline">{_e(headline)}</div>']
    if why:
        out.append(f'<div class="why"><b>למה זה חשוב:</b> {_e(why)}</div>')
    out.append("</div>")
    if chart:
        move = chart.get("change_pct")
        shown = f"{'+' if move > 0 else ''}{move:.1f}%" if isinstance(move, (int, float)) and math.isfinite(move) else ""
        tone = UP if isinstance(move, (int, float)) and move >= 0 else DOWN
        out.append(f'<div class="chart"><div class="head"><span class="sym">{_e(chart["symbol"])}</span>'
                   f'<span class="close">{chart["close"]:,.2f}</span>'
                   f'<span class="move" style="color:{tone}">{_e(shown)}</span></div>'
                   f'{_candles(chart["rows"])}'
                   f'<div class="note">שלושה חודשים, עד הסגירה ב-{_e(chart["last_day"])}</div></div>')
    out.append('</div><div class="foot"><span>חדשות מ-X, בעברית</span><span>לא ייעוץ השקעות</span></div>'
               '</div></body></html>')
    return "".join(out), WIDTH, HEIGHT


def to_png(folder: Path, name: str, **story: Any) -> Path:
    """One story's card as a PNG, drawn by the browser (tascreen/analyst/png.py)."""
    from .analyst.png import html_to_png

    page, width, height = render_html(**story)
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / f"{name}.html"
    source.write_text(page, encoding="utf-8")
    return html_to_png(source, folder / f"{name}.png", width=width, height=height)
