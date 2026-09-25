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
from tascreen.tv.mcp_client import FileTokenStorage


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
    session = _Session()
    session.screener.rate_limited = 99                  # the screener answers 429 throughout
    object.__setattr__(settings.tradingview, "rate_limit_delays", (0,))
    limited = probe(settings, FakeClient(session), lambda: llm, calls=5)
    assert limited["tradingview"]["ok"] and limited["tradingview"]["ohlcv"]["ok"] == 5
    assert limited["tradingview"]["screener"] == {"ok": False, "error": "RateLimited",
                                                   "seconds": limited["tradingview"]["screener"]["seconds"]}
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

    class Slow(_Session):
        async def call_tool(self, name, args):
            import time as _time
            _time.sleep(0.05)
            return await super().call_tool(name, args)

    report = probe(settings, FakeClient(Slow()), lambda: SyntheticLLM(lambda *a: {"ok": True}),
                   calls=500, budget_s=0.2)
    assert report["tradingview"]["ohlcv"]["calls"] < 500
    assert "time budget" in report["tradingview"]["ohlcv_stopped_early"]


def test_the_token_path_can_come_from_the_environment(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("tradingview:\n  token_path: '~/a.json'\n", encoding="utf-8")
    monkeypatch.setenv("TA_TV_TOKEN_PATH", "/state/tv_tokens.json")
    assert load_settings(tmp_path / "config.yaml", root=tmp_path).tradingview.token_path == \
        "/state/tv_tokens.json"


def test_saving_new_tokens_prints_nothing(capsys, monkeypatch, tmp_path):
    """On Actions, stdout goes to files; a token must never reach them (not even as a mask)."""
    from mcp.shared.auth import OAuthToken

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    storage = FileTokenStorage(tmp_path / "t.json")
    asyncio.run(storage.set_tokens(OAuthToken(access_token="acc-9", token_type="Bearer",
                                              refresh_token="ref-9")))
    captured = capsys.readouterr()
    assert "acc-9" not in captured.out + captured.err and "ref-9" not in captured.out + captured.err


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


# ------------------------------------------------------------------ the tick
def _tick_setup(tmp_path, monkeypatch, deferred=0):
    import run
    from test_web_live import _setup

    settings, store, day = _setup(tmp_path)
    calls = {"update": 0, "channels": 0}

    def fake_update(s, limit, stop_at=None):
        calls["update"] += 1
        s.log_dir.mkdir(parents=True, exist_ok=True)
        (s.log_dir / "bars_last_run.json").write_text(
            json.dumps({"counts": {"updated": 2, **({"deferred": deferred} if deferred else {})}}),
            encoding="utf-8")
        return 0

    def fake_channels(s, force=False, writer=None):
        calls["channels"] += 1
        (s.log_dir / "channels_last_run.json").write_text(
            json.dumps({"channels": {"general": {"threads": 1}, "double_top": "failed: x"}}),
            encoding="utf-8")
        return 1

    monkeypatch.setattr(run, "update", fake_update)
    monkeypatch.setattr(run, "channels", fake_channels)
    monkeypatch.setattr(run, "_target", lambda s: day)
    return run, settings, store, day, calls


def test_the_tick_runs_the_daily_update_once_per_session(tmp_path, monkeypatch):
    run, settings, store, day, calls = _tick_setup(tmp_path, monkeypatch)
    assert run.ci_tick(settings, None, None, with_channels=True) == 0
    summary = json.loads((settings.log_dir / "ci_summary.json").read_text(encoding="utf-8"))
    assert summary["due"] and summary["complete"] and summary["session"] == day.isoformat()
    assert summary["channels"] == {"written": 1, "already": 0, "failed": 1, "skipped": 0}
    assert summary["scan"]["symbols_scanned"] == 2
    assert run.ci_tick(settings, None, None, with_channels=True) == 0       # nothing due now
    assert calls == {"update": 1, "channels": 1}
    text = (settings.log_dir / "ci_summary.json").read_text(encoding="utf-8")
    assert "NYSE:HS" not in text and "close" not in text                    # counts only


def test_the_tick_sends_the_breakout_report_once_and_logs_counts_only(tmp_path, monkeypatch):
    import tascreen.notify

    run, settings, store, day, calls = _tick_setup(tmp_path, monkeypatch)
    sent = []

    class Bot:
        def send(self, text, html=False):
            sent.append(text)

    monkeypatch.setattr(tascreen.notify, "from_environment", lambda: Bot())
    run.ci_tick(settings, None, None, with_channels=False)
    summary = json.loads((settings.log_dir / "ci_summary.json").read_text(encoding="utf-8"))
    assert summary["alerts"]["status"] == "sent" and sent and "פריצות שוריות" in sent[0]
    assert set(summary["alerts"]) == {"status", "breakouts", "verge", "analyses", "messages",
                                      "intraday_held", "intraday_fell"}
    run.ci_tick(settings, None, None, with_channels=False)
    summary = json.loads((settings.log_dir / "ci_summary.json").read_text(encoding="utf-8"))
    assert summary["alerts"] == {"status": "already sent"} and len(sent) == 1


def test_without_a_bot_the_tick_only_says_so(tmp_path, monkeypatch):
    run, settings, store, day, calls = _tick_setup(tmp_path, monkeypatch)
    run.ci_tick(settings, None, None, with_channels=False)
    summary = json.loads((settings.log_dir / "ci_summary.json").read_text(encoding="utf-8"))
    assert summary["alerts"] == {"status": "telegram not configured"}


def test_a_cut_short_update_is_finished_by_the_next_tick(tmp_path, monkeypatch):
    run, settings, store, day, calls = _tick_setup(tmp_path, monkeypatch, deferred=5)
    assert run.ci_tick(settings, None, 30, with_channels=False) == 1
    assert store.read_live_state()["complete"] is False
    run.ci_tick(settings, None, 30, with_channels=False)
    assert calls == {"update": 2, "channels": 0}


def test_a_refreshed_token_is_pushed_to_the_state_repo(tmp_path, monkeypatch):
    import subprocess

    from mcp.shared.auth import OAuthToken

    def git(*args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout

    origin, state = tmp_path / "origin.git", tmp_path / "state"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("clone", "-q", str(origin), str(state))
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty",
        "-m", "seed", cwd=state)
    git("push", "-q", "origin", "HEAD:main", cwd=state)
    git("config", "user.name", "t", cwd=state)
    git("config", "user.email", "t@example.invalid", cwd=state)
    monkeypatch.setenv("TA_STATE_DIR", str(state))
    storage = FileTokenStorage(state / "tv_tokens.json")
    asyncio.run(storage.set_tokens(OAuthToken(access_token="a", token_type="Bearer", refresh_token="r")))
    assert "tv_tokens.json" in git("ls-tree", "-r", "--name-only", "main", cwd=origin)
