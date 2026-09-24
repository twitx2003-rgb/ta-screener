"""The exported static site: every page, links that resolve, the same bytes for the
same data, the size guard. Synthetic data only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tascreen.outcomes import update
from tascreen.web import fmt
from tascreen.web.export import ExportError, compact_html, export_site
from test_channels import _writer
from test_web_live import RULES, _setup


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("site")
    settings, store, day = _setup(tmp / "root")
    from tascreen.web.data import ScanRepository
    _writer(store).daily(ScanRepository(store, RULES).current(), progress=lambda m: None)
    update(store, 60)
    report = export_site(settings, tmp / "out", rules=RULES)
    return settings, tmp / "out", report


def _target(out: Path, href: str) -> Path | None:
    path = href.split("#", 1)[0]
    if not path:
        return None
    if path.startswith("/static/") or path.startswith("/data/"):
        return out / path.lstrip("/")
    return out / path.lstrip("/") / "index.html" if path != "/" else out / "index.html"


def test_every_page_is_written_clean(site):
    settings, out, report = site
    assert report["pages"] == len(list(out.rglob("index.html")))
    for name in ("index.html", "screener/index.html", "scorecard/index.html", "status/index.html",
                 "patterns/index.html", "c/head_shoulders_top/index.html",
                 "screener/pattern/double_bottom/index.html",
                 "screener/preset/chart-forming/index.html", "symbol/NYSE_HS/index.html",
                 "symbol/NASDAQ_DB/index.html", "404.html", "static/app.css",
                 "static/symbol.js", "data/stamp.json", "vercel.json", "api/telegram.js"):
        assert (out / name).exists(), name
    for page in out.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert fmt.page_problems(html) == [], page
        assert "run.py" not in html and "/api/" not in html, page


def test_internal_links_resolve_and_carry_no_query(site):
    _, out, _ = site
    for page in out.rglob("*.html"):
        for href in re.findall(r'(?:href|data-href|src)="(/[^"]*)"', page.read_text(encoding="utf-8")):
            assert "?" not in href, (page, href)
            target = _target(out, href)
            assert target is None or target.exists(), (page, href)


def test_pages_embed_the_stamp_and_the_symbol_chart_is_compact(site):
    _, out, report = site
    stamp = json.loads((out / "data/stamp.json").read_text(encoding="utf-8"))["stamp"]
    assert stamp == report["stamp"] and f'"{stamp}"' in (out / "index.html").read_text(encoding="utf-8")
    html = (out / "symbol/NYSE_HS/index.html").read_text(encoding="utf-8")
    chart = json.loads(re.search(r'id="chart-data">(.*?)</script>', html, re.S).group(1))
    assert set(chart["cols"]) == {"t", "o", "h", "l", "c", "v", "sma50", "sma150"}
    assert len(chart["cols"]["t"]) <= 200 or chart["detections"]
    assert "/data/stamp.json" in html


def test_the_same_data_gives_the_same_files(site, tmp_path):
    settings, out, _ = site
    again = tmp_path / "again"
    export_site(settings, again, rules=RULES)
    first = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    second = {p.relative_to(again): p.read_bytes() for p in again.rglob("*") if p.is_file()}
    assert first == second


def test_the_size_guard_and_the_vercel_config(site, tmp_path):
    settings, out, _ = site
    config = json.loads((out / "vercel.json").read_text(encoding="utf-8"))
    assert config["trailingSlash"] is True and config["rewrites"][0]["source"] == "/screener/"
    with pytest.raises(ExportError, match="over the"):
        export_site(settings, tmp_path / "small", rules=RULES, max_bytes=10_000)


def test_compacting_keeps_pre_blocks():
    assert compact_html(b"<p>\n    a\n\n    b\n</p>") == b"<p>\na\nb\n</p>"
    assert compact_html(b"<pre>\n  x\n</pre>") == b"<pre>\n  x\n</pre>"
