"""The website as static files, for Vercel (stage 2 of the GitHub Actions + Vercel plan).

Every page comes from the same FastAPI app in static mode (create_app(static=True)),
fetched through TestClient and written as a folder index:

    /                          index.html (#כללי)
    /c/<channel>/              every pattern's channel
    /screener/                 the default view (the first STATIC_ROWS rows)
    /screener/pattern/<key>/   one per pattern      /screener/preset/<slug>/  the quick filters
    /symbol/<EXCHANGE_TICKER>/ every stock (compact chart data)
    /patterns/  /scorecard/  /status/  and 404.html

plus /static/ (copied), data/stamp.json (the reload stamp every page embeds) and
vercel.json (trailing slashes, security headers, a rewrite for ?pattern= links).
There is no live data yet (a later stage adds it in the browser).

The same data gives byte-identical files: the stamp is a hash of the content, not of
file times, so a deployment uploads only what changed. The site must stay under
Vercel's 100 MB for a CLI deployment; the export refuses to exceed MAX_BYTES.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from ..channels.select import GENERAL
from ..config import Settings
from ..errors import ScreenerError
from ..patterns.rules import Rules, load_rules
from ..store import Store, symbol_file_stem
from .app import HOST, PRESETS, WEB_DIR, create_app
from .data import ScanRepository

MAX_BYTES = 85 * 1024 * 1024
NOT_FOUND_URL = "/__not_found__"          # static mode only

VERCEL_CONFIG = {
    "trailingSlash": True,
    "rewrites": [{"source": "/screener/",
                  "has": [{"type": "query", "key": "pattern", "value": "(?<p>[a-z_]+)"}],
                  "destination": "/screener/pattern/:p/"}],
    "headers": [
        {"source": "/(.*)", "headers": [
            {"key": "X-Content-Type-Options", "value": "nosniff"},
            {"key": "Referrer-Policy", "value": "same-origin"},
            {"key": "Content-Security-Policy", "value": "frame-ancestors 'none'"}]},
        {"source": "/data/(.*)", "headers": [{"key": "Cache-Control", "value": "no-cache"}]},
    ],
}


class ExportError(ScreenerError):
    pass


def content_stamp(store: Store, days_shown: int) -> str:
    """A hash of what the pages show: the newest scan, the shown channel days, the ledger."""
    digest = hashlib.sha256()
    scans = store.scan_days()
    if scans:
        digest.update((store.scans_dir / scans[-1].isoformat() / "scan.json").read_bytes())
    for day in store.channel_days()[-days_shown:]:
        for path in sorted((store.channels_dir / day.isoformat()).glob("*.json")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    meta = store.outcomes_dir / "meta.json"
    if meta.exists():
        digest.update(meta.read_bytes())
    return digest.hexdigest()[:16]


def pages(symbols: list[str], rules: Rules) -> list[tuple[str, str]]:
    """(URL to fetch, file to write) for every page of the site."""
    keys = [*rules.chart, *rules.candle]
    out = [("/", "index.html"), ("/screener", "screener/index.html"),
           ("/patterns", "patterns/index.html"), ("/scorecard", "scorecard/index.html"),
           ("/status", "status/index.html")]
    out += [(f"/c/{key}", f"c/{key}/index.html") for key in (GENERAL, *keys)]
    out += [(f"/screener?pattern={key}", f"screener/pattern/{key}/index.html") for key in keys]
    out += [(f"/screener?{query}", f"screener/preset/{slug}/index.html") for slug, query, _ in PRESETS]
    out += [(f"/symbol/{s}", f"symbol/{symbol_file_stem(s)}/index.html") for s in symbols]
    return out


def export_site(settings: Settings, out: Path, *, rules: Rules | None = None,
                max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    """Write the whole site to `out` (replaced). Returns counts and the total size."""
    from fastapi.testclient import TestClient

    rules = rules or load_rules()
    store = Store(settings.data_dir)
    view = ScanRepository(store, rules).current()
    if view is None:
        raise ExportError("no scan yet: nothing to export")
    stamp = content_stamp(store, settings.channels.days_shown)
    client = TestClient(create_app(settings, rules=rules, static=True, static_stamp=stamp),
                        base_url=f"http://{HOST}")
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    written = 0
    for url, name in pages(sorted(view.stocks["symbol"]), rules):
        response = client.get(url)
        if response.status_code != 200:
            raise ExportError(f"{url}: HTTP {response.status_code}")
        _write(out / name, compact_html(response.content))
        written += 1
    missing = client.get(NOT_FOUND_URL)
    _write(out / "404.html", compact_html(missing.content))
    shutil.copytree(WEB_DIR / "static", out / "static")
    _write(out / "data" / "stamp.json", json.dumps({"stamp": stamp}).encode())
    _write(out / "vercel.json", json.dumps(VERCEL_CONFIG, indent=2).encode())
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    if size > max_bytes:
        raise ExportError(f"the site is {size / 2**20:.1f} MB, over the {max_bytes / 2**20:.0f} MB "
                          "limit (Vercel takes 100 MB per CLI deployment)")
    return {"pages": written, "files": sum(1 for p in out.rglob("*") if p.is_file()),
            "megabytes": round(size / 2**20, 1), "stamp": stamp}


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def compact_html(html: bytes) -> bytes:
    """Drop the templates' indentation and blank lines (a newline still separates
    inline elements, so the page renders the same). Pages with <pre> are left alone."""
    if b"<pre" in html:
        return html
    return b"\n".join(line.strip() for line in html.splitlines() if line.strip())
