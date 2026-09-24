"""GitHub Actions support: the stage-0 probe, the token-path override, log masking.
Offline fakes only (synthetic values)."""
from __future__ import annotations

import asyncio
import json
from datetime import date

from fakes import FakeClient, FakeOhlcv, FakeScreener, stock_row
from tascreen.ci import probe, refresh_fingerprint
from tascreen.config import Settings, load_settings
from tascreen.llm import SyntheticLLM
from tascreen.tv.mcp_client import FileTokenStorage, mask_in_actions


class _Session:
    """Screener calls to one fake, get-ohlcv to the other; can rotate the token on use."""

    def __init__(self, token_path=None, rotate=False):
        self.screener = FakeScreener([stock_row(i, 2e9 + i * 1e7) for i in range(40)])
        self.ohlcv = FakeOhlcv(date(2026, 9, 23))
        self.token_path, self.rotate = token_path, rotate

    async def call_tool(self, name, args):
        if self.rotate:
            FileTokenStorage(self.token_path)._write(
                "tokens", {"access_token": "a2", "token_type": "Bearer", "refresh_token": "r2"})
            self.rotate = False
        fake = self.ohlcv if name == "mcp-tv-get-ohlcv" else self.screener
        return await fake.call_tool(name, args)


def _settings(tmp_path):
    token = tmp_path / "tv_tokens.json"
    token.write_text(json.dumps({"tokens": {"access_token": "a1", "token_type": "Bearer",
                                            "refresh_token": "r1"}, "tokens_saved_at": 9e9}),
                     encoding="utf-8")
    settings = Settings(root=tmp_path)
    object.__setattr__(settings.tradingview, "token_path", str(token))
    return settings, token


def test_probe_reports_counts_and_timings_only(tmp_path):
    settings, token = _settings(tmp_path)
    llm = SyntheticLLM(lambda system, user, schema: {"ok": True})
    report = probe(settings, FakeClient(_Session()), lambda: llm, calls=25)
    assert report["tradingview"]["ok"] and report["tradingview"]["screener"]["stocks"] == 40
    assert report["tradingview"]["ohlcv"]["calls"] == 25 and report["tradingview"]["ohlcv"]["ok"] == 25
    assert report["refresh_token"] == "kept" and report["claude"] == {"ok": True, "served_by": "synthetic"}
    text = json.dumps(report)
    assert "S00" not in text and "r1" not in text and "a1" not in text      # no symbols, no tokens
    assert json.loads(token.read_text(encoding="utf-8"))["tokens_saved_at"] == 0.0  # refresh forced


def test_probe_sees_a_rotated_refresh_token_and_names_failures(tmp_path):
    settings, token = _settings(tmp_path)
    before = refresh_fingerprint(token)
    report = probe(settings, FakeClient(_Session(token, rotate=True)),
                   lambda: SyntheticLLM(lambda *a: {"ok": True}), calls=3)
    assert report["refresh_token"] == "rotated" and refresh_fingerprint(token) != before

    class Broken:
        def with_session(self, work):
            raise ConnectionError("403 from https://example.invalid with a payload")

    def no_claude():
        raise FileNotFoundError("claude")

    report = probe(settings, Broken(), no_claude, calls=3)
    assert report["tradingview"]["ok"] is False and report["tradingview"]["error"] == "ConnectionError"
    assert report["claude"] == {"ok": False, "error": "FileNotFoundError"}
    assert "payload" not in json.dumps(report)


def test_the_token_path_can_come_from_the_environment(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("tradingview:\n  token_path: '~/a.json'\n", encoding="utf-8")
    monkeypatch.setenv("TA_TV_TOKEN_PATH", "/state/tv_tokens.json")
    assert load_settings(tmp_path / "config.yaml", root=tmp_path).tradingview.token_path == \
        "/state/tv_tokens.json"


def test_new_tokens_are_masked_only_on_actions(capsys, monkeypatch, tmp_path):
    from mcp.shared.auth import OAuthToken

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    mask_in_actions("secret-1")
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    storage = FileTokenStorage(tmp_path / "t.json")
    asyncio.run(storage.set_tokens(OAuthToken(access_token="acc-9", token_type="Bearer",
                                              refresh_token="ref-9")))
    out = capsys.readouterr().out
    assert "::add-mask::acc-9" in out and "::add-mask::ref-9" in out


def test_the_vercel_probe_site_and_its_checks(tmp_path):
    from tascreen.ci import PROBE_PAGES, check_site, probe_site

    probe_site(tmp_path)
    assert all((tmp_path / name).exists() for name in PROBE_PAGES)
    config = json.loads((tmp_path / "vercel.json").read_text(encoding="utf-8"))
    assert config["rewrites"][0]["destination"] == "/screener/pattern/:p/"

    def fetch(url):
        path = url.split("example.org", 1)[1]
        if path.startswith("/screener?pattern="):
            name = "screener/pattern/double_top/index.html"
        elif path.startswith("/symbol/NYSE_BRK.B"):
            name = "symbol/NYSE_BRK.B/index.html"
        elif path == "/":
            name = "index.html"
        else:
            return 404, (tmp_path / "404.html").read_text(encoding="utf-8")
        return 200, (tmp_path / name).read_text(encoding="utf-8")

    assert all(r["ok"] for r in check_site("https://example.org", fetch))
