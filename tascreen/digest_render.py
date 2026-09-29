"""The morning digest as an infographic (owner, 2026-09-29): a light, designed image of the
news from the last session's open to 10:00 Israel time, for the Telegram group.

An HTML page with fixed-height blocks (every text is clamped to its lines), so the page's
height is known before the shot, and the browser (Chrome on the runner, Edge here) takes
the PNG at exactly that size. The font is Heebo (SIL Open Font License, tascreen/fonts/),
so the picture looks the same everywhere. No emoji in the picture: the runner has no emoji
font; the signs are drawn with CSS.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

FONT = Path(__file__).with_name("fonts") / "Heebo-Variable.ttf"
WIDTH = 1080

# category -> (Hebrew label, colour)
CATEGORIES = {
    "macro": ("מאקרו", "#2563eb"),
    "fed": ("הפד וריבית", "#7c3aed"),
    "bonds": ("אג\"ח", "#0e7490"),
    "earnings": ("דוחות", "#0d9488"),
    "company": ("חברות", "#ea580c"),
    "tech": ("טכנולוגיה ו-AI", "#4f46e5"),
    "sector": ("סקטורים", "#d97706"),
    "energy": ("אנרגיה וסחורות", "#a16207"),
    "geopolitics": ("גיאופוליטיקה", "#dc2626"),
    "market": ("השוק", "#475569"),
    "crypto": ("קריפטו", "#9333ea"),
}

# fixed heights (px); the page is their sum
PAD, GAP = 44, 22
HEADER, STRIP, HEADLINE, ITEM, ITEM_GAP, WATCH, FOOTER = 176, 132, 158, 164, 14, 124, 58


def height(items: int, *, strip: bool, watch: bool) -> int:
    h = PAD * 2 + HEADER + GAP + HEADLINE + GAP + items * ITEM + max(0, items - 1) * ITEM_GAP + GAP + FOOTER
    return h + (STRIP + GAP if strip else 0) + (WATCH + GAP if watch else 0)


def _e(text: Any) -> str:
    return html.escape(str(text or ""))


def _pct(value: float) -> str:
    return f"{'+' if value > 0 else ''}{value:.2f}%"


CSS = """
@font-face { font-family: Heebo; src: url("%(font)s"); font-weight: 100 900; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: %(w)dpx; height: %(h)dpx; overflow: hidden; }
body { background: #eef1f7; font-family: Heebo, "Segoe UI", Arial, sans-serif; color: #0f172a;
       padding: %(pad)dpx; direction: rtl; }
.block { background: #ffffff; border-radius: 20px; box-shadow: 0 1px 2px rgba(15,23,42,.06),
         0 4px 14px rgba(15,23,42,.05); overflow: hidden; }
.gap { height: %(gap)dpx; }
.ltr { direction: ltr; unicode-bidi: isolate; display: inline-block; }

.header { height: %(header)dpx; padding: 30px 36px; position: relative;
          background: linear-gradient(135deg, #0f2a5f 0%%, #1d4ed8 100%%); color: #ffffff; }
.header .kicker { font-size: 24px; font-weight: 500; opacity: .85; letter-spacing: .5px; }
.header .title { font-size: 56px; font-weight: 800; line-height: 1.15; margin-top: 4px; }
.header .when { position: absolute; left: 36px; top: 34px; text-align: left; }
.header .date { font-size: 30px; font-weight: 700; }
.header .window { font-size: 21px; opacity: .85; margin-top: 6px; max-width: 420px; }

.strip { height: %(strip)dpx; display: flex; gap: 16px; }
.tile { flex: 1; border-radius: 18px; padding: 20px 22px; background: #ffffff;
        box-shadow: 0 1px 2px rgba(15,23,42,.06), 0 4px 14px rgba(15,23,42,.05); }
.tile .name { font-size: 22px; color: #5b6477; font-weight: 600; }
.tile .move { font-size: 44px; font-weight: 800; margin-top: 8px; }
.tile.up .move { color: #059669; } .tile.down .move { color: #dc2626; } .tile.flat .move { color: #475569; }
.tile .arrow { display: inline-block; width: 0; height: 0; border-left: 11px solid transparent;
               border-right: 11px solid transparent; margin-left: 10px; vertical-align: middle; }
.tile.up .arrow { border-bottom: 16px solid #059669; } .tile.down .arrow { border-top: 16px solid #dc2626; }
.strip-note { font-size: 19px; color: #5b6477; margin: -8px 4px 0; height: 0; }

.headline { height: %(headline)dpx; padding: 24px 32px; border-right: 10px solid #1d4ed8; }
.label { font-size: 21px; font-weight: 700; color: #1d4ed8; letter-spacing: .3px; }
.headline .text { font-size: 31px; font-weight: 600; line-height: 1.38; margin-top: 6px;
                  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

.item { height: %(item)dpx; padding: 20px 28px 18px; display: flex; gap: 22px; align-items: flex-start; }
.item + .item { margin-top: %(item_gap)dpx; }
.num { flex: 0 0 52px; height: 52px; border-radius: 14px; font-size: 26px; font-weight: 800;
       display: flex; align-items: center; justify-content: center; color: #ffffff; margin-top: 4px; }
.body { flex: 1; min-width: 0; }
.row { display: flex; align-items: center; gap: 12px; height: 44px; }
.chip { font-size: 19px; font-weight: 700; padding: 3px 14px; border-radius: 999px; white-space: nowrap; }
.title { font-size: 31px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1;
         min-width: 0; }
.detail { font-size: 25px; line-height: 1.36; color: #334155; margin-top: 6px;
          display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tickers { display: flex; gap: 8px; flex: 0 0 auto; }
.ticker { direction: ltr; font-size: 20px; font-weight: 700; color: #0f172a; background: #eef1f7;
          border: 1px solid #dfe4ee; border-radius: 10px; padding: 2px 10px; }

.watch { height: %(watch)dpx; padding: 22px 32px; background: #fff8eb; border-right: 10px solid #d97706; }
.watch .label { color: #b45309; }
.watch .text { font-size: 27px; font-weight: 600; line-height: 1.38; margin-top: 6px; color: #3b2a0a;
               display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

.footer { height: %(footer)dpx; display: flex; align-items: center; justify-content: space-between;
          font-size: 20px; color: #5b6477; padding: 0 8px; }
"""


def render_html(d: dict[str, Any]) -> tuple[str, int, int]:
    """(the page, its width, its height) for a digest:
    {kicker, date_he, window_he, indexes: [{name, change}], indexes_note, headline_he,
     items: [{category, title_he, detail_he, tickers}], watch_he, footer_he}."""
    items = list(d.get("items") or [])[:8]
    strip = [i for i in d.get("indexes") or [] if isinstance(i.get("change"), (int, float))][:4]
    watch = str(d.get("watch_he") or "").strip()
    h = height(len(items), strip=bool(strip), watch=bool(watch))
    css = CSS % {"font": FONT.resolve().as_uri(), "w": WIDTH, "h": h, "pad": PAD, "gap": GAP,
                 "header": HEADER, "strip": STRIP, "headline": HEADLINE, "item": ITEM, "item_gap": ITEM_GAP,
                 "watch": WATCH, "footer": FOOTER}
    out = [f'<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><style>{css}</style></head><body>',
           '<div class="block header">',
           f'<div class="kicker">{_e(d.get("kicker") or "וול סטריט · סיכום בוקר")}</div>',
           f'<div class="title">{_e(d.get("title_he") or "מה קרה מאז הפתיחה")}</div>',
           f'<div class="when"><div class="date">{_e(d.get("date_he"))}</div>'
           f'<div class="window">{_e(d.get("window_he"))}</div></div>',
           '</div><div class="gap"></div>']
    if strip:
        tiles = []
        for i in strip:
            move = float(i["change"])
            kind = "up" if move > 0.004 else "down" if move < -0.004 else "flat"
            tiles.append(f'<div class="tile {kind}"><div class="name">{_e(i.get("name"))}</div>'
                         f'<div class="move"><span class="ltr">{_e(_pct(move))}</span><span class="arrow"></span>'
                         f'</div></div>')
        out += [f'<div class="strip">{"".join(tiles)}</div>', '<div class="gap"></div>']
    out += ['<div class="block headline"><div class="label">בשורה התחתונה</div>',
            f'<div class="text">{_e(d.get("headline_he"))}</div></div>', '<div class="gap"></div>']
    for n, item in enumerate(items, 1):
        label, colour = CATEGORIES.get(str(item.get("category")), CATEGORIES["market"])
        tickers = "".join(f'<span class="ticker">{_e(t)}</span>' for t in (item.get("tickers") or [])[:2])
        out.append(f'<div class="block item"><div class="num" style="background:{colour}">{n}</div>'
                   f'<div class="body"><div class="row">'
                   f'<span class="chip" style="color:{colour};background:{colour}14">{_e(label)}</span>'
                   f'<span class="title">{_e(item.get("title_he"))}</span>'
                   f'<span class="tickers">{tickers}</span></div>'
                   f'<div class="detail">{_e(item.get("detail_he"))}</div></div></div>')
    if watch:
        out += ['<div class="gap"></div>', '<div class="block watch"><div class="label">מה לשים לב היום</div>',
                f'<div class="text">{_e(watch)}</div></div>']
    out += ['<div class="gap"></div>',
            f'<div class="footer"><span>{_e(d.get("footer_he"))}</span><span>לא ייעוץ השקעות</span></div>',
            '</body></html>']
    return "".join(out), WIDTH, h


def to_png(d: dict[str, Any], folder: Path) -> Path:
    """The digest's picture, drawn by the browser (tascreen/analyst/png.py)."""
    from .analyst.png import html_to_png

    page, width, h = render_html(d)
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "digest.html"
    source.write_text(page, encoding="utf-8")
    return html_to_png(source, folder / "digest.png", width=width, height=h)
