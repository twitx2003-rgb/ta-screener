"""Running on GitHub Actions (owner's decision 2026-09-24: GitHub Actions + Vercel).

Stage 0, the probe: before anything is moved, check from a GitHub runner that
- TradingView serves a cloud machine: a forced token refresh, one full screener pass
  (the universe bands), then `calls` get-ohlcv calls, timed;
- whether TradingView rotates the refresh token (it decides how the token must be kept);
- Claude Code answers with the subscription token (CLAUDE_CODE_OAUTH_TOKEN).
The public repo's Actions logs are public, so the probe reports counts, timings and
error class names only: no prices, no symbols' data, no token material.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .errors import ProviderError
from .tv.data import OHLCV_TOOL, RateLimited, bars_frame, fetch_in_session
from .tv.mcp_client import FileTokenStorage
from .universe import fetch_universe

PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"], "additionalProperties": False}


def refresh_fingerprint(path: Path) -> str | None:
    """A short hash of the stored refresh token, only ever compared, never shown."""
    try:
        tokens = json.loads(Path(path).expanduser().read_text(encoding="utf-8")).get("tokens") or {}
    except (OSError, ValueError):
        return None
    token = tokens.get("refresh_token")
    return hashlib.sha256(token.encode()).hexdigest()[:16] if token else None


def force_refresh(path: Path) -> None:
    """Make the stored access token look expired, so the next call refreshes it."""
    storage = FileTokenStorage(path)
    storage._write("tokens_saved_at", 0.0)


def _timings(seconds: list[float]) -> dict[str, float | None]:
    if not seconds:
        return {"median_s": None, "p90_s": None, "max_s": None}
    ordered = sorted(seconds)
    return {"median_s": round(statistics.median(ordered), 2),
            "p90_s": round(ordered[int(0.9 * (len(ordered) - 1))], 2),
            "max_s": round(ordered[-1], 2)}


async def _tradingview(session, settings: Settings, calls: int) -> dict[str, Any]:
    delays = settings.tradingview.rate_limit_delays
    started = time.monotonic()
    frame, summary = await fetch_universe(session, settings.universe, delays=delays)
    out: dict[str, Any] = {"screener": {"ok": True, "stocks": int(len(frame)),
                                        "bands": len(summary["bands"]),
                                        "seconds": round(time.monotonic() - started, 1)}}
    seconds, counts = [], {"ok": 0, "rate_limited": 0, "failed": 0}
    for symbol in frame["symbol"].head(calls):
        t = time.monotonic()
        try:
            bars_frame(await fetch_in_session(session, OHLCV_TOOL,
                                              {"symbol": symbol, "interval": "1D", "count": 5},
                                              delays=delays), symbol)
            counts["ok"] += 1
        except RateLimited:
            counts["rate_limited"] += 1
        except ProviderError:
            counts["failed"] += 1
        seconds.append(time.monotonic() - t)
    out["ohlcv"] = {"calls": len(seconds), **counts, **_timings(seconds)}
    return out


def probe(settings: Settings, client: Any, llm_factory: Callable[[], Any], *,
          calls: int = 300) -> dict[str, Any]:
    """The stage-0 report (see the module notes)."""
    path = Path(settings.tradingview.token_path).expanduser()
    report: dict[str, Any] = {"token_file": path.exists()}
    before = refresh_fingerprint(path)
    if path.exists():
        force_refresh(path)
    started = time.monotonic()
    try:
        report["tradingview"] = client.with_session(lambda s: _tradingview(s, settings, calls))
        report["tradingview"]["ok"] = True
    except Exception as exc:  # noqa: BLE001 — any failure, by class name only (messages can quote payloads)
        report["tradingview"] = {"ok": False, "error": type(exc).__name__}
    report["tradingview"]["seconds"] = round(time.monotonic() - started, 1)
    after = refresh_fingerprint(path)
    report["refresh_token"] = ("missing" if after is None else
                               "rotated" if before is not None and after != before else "kept")
    try:
        parsed, usage = llm_factory().complete(
            system="You are a connectivity check. Answer with the JSON the schema asks for.",
            user="Reply with ok set to true.", schema=PROBE_SCHEMA)
        report["claude"] = {"ok": parsed.get("ok") is True, "served_by": usage.get("served_by")}
    except Exception as exc:  # noqa: BLE001
        report["claude"] = {"ok": False, "error": type(exc).__name__}
    return report


# ------------------------------------------------------------------ Vercel probe
PROBE_PAGES = {
    "index.html": "בדיקת פריסה: דף הבית",
    "symbol/NYSE_BRK.B/index.html": "בדיקת פריסה: דף מניה עם נקודה בשם",
    "screener/pattern/double_top/index.html": "בדיקת פריסה: סורק לפי תבנית",
    "404.html": "בדיקת פריסה: הדף לא נמצא",
}
PROBE_CHECKS = (("/", 200, "דף הבית"), ("/symbol/NYSE_BRK.B/", 200, "נקודה בשם"),
                ("/symbol/NYSE_BRK.B", 200, "נקודה בשם"), ("/no/such/page/", 404, "לא נמצא"),
                ("/screener?pattern=double_top", 200, "לפי תבנית"))


def probe_site(folder: Path) -> None:
    """A tiny static site with the URL shapes the real export will use."""
    folder = Path(folder)
    for name, text in PROBE_PAGES.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8">'
                        f"<title>probe</title><p>{text}</p></html>", encoding="utf-8")
    config = {
        "trailingSlash": True,
        "rewrites": [{"source": "/screener",
                      "has": [{"type": "query", "key": "pattern", "value": "(?<p>[a-z_]+)"}],
                      "destination": "/screener/pattern/:p/"}],
        "headers": [{"source": "/(.*)", "headers": [
            {"key": "X-Content-Type-Options", "value": "nosniff"},
            {"key": "Referrer-Policy", "value": "same-origin"},
            {"key": "Content-Security-Policy", "value": "frame-ancestors 'none'"}]}],
    }
    (folder / "vercel.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


def check_site(base_url: str, fetch: Callable[[str], tuple[int, str]]) -> list[dict[str, Any]]:
    """Status of each probe URL (`fetch(url) -> (status, body)`), and whether the body
    is the page expected."""
    out = []
    for path, want_status, marker in PROBE_CHECKS:
        status, body = fetch(base_url.rstrip("/") + path)
        out.append({"path": path, "status": status, "ok": status == want_status and marker in body})
    return out


def _fetch(url: str) -> tuple[int, str]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "probe"}),
                                    timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, type(exc).__name__


if __name__ == "__main__":        # used by .github/workflows/probe.yml
    import sys

    if sys.argv[1] == "probe-site":
        probe_site(Path(sys.argv[2]))
    elif sys.argv[1] == "check-site":
        results = check_site(sys.argv[2], _fetch)
        print(json.dumps(results, indent=2, ensure_ascii=False))
        sys.exit(0 if all(r["ok"] for r in results) else 1)
