import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import logging

import pytest


@pytest.fixture(autouse=True)
def _no_real_messages(monkeypatch, tmp_path_factory):
    """Tests never reach the owner's Telegram bot or GitHub: no keys from the environment,
    and not the bot saved on this computer (~/.ta-screener/telegram.json). (Without this,
    a tick test once sent the owner a real report of made-up stocks.)"""
    import tascreen.notify
    import tascreen.xnews

    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GH_DISPATCH_TOKEN", "X_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tascreen.notify, "CREDENTIALS",
                        tmp_path_factory.getbasetemp() / "no-telegram.json")
    monkeypatch.setattr(tascreen.xnews, "CREDENTIALS", tmp_path_factory.getbasetemp() / "no-x.json")


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch):
    """Tests never start the real Claude Code: without an explicit command, making one
    fails as it does where Claude is not installed. (The research team runs from the
    nightly tick; from a normal terminal a test would otherwise have called it.)"""
    import tascreen.llm
    from tascreen.errors import ConfigError

    def missing():
        raise ConfigError("Claude Code is not available in tests")

    monkeypatch.setattr(tascreen.llm.ClaudeCodeLLM, "find_cli", staticmethod(missing))


@pytest.fixture(autouse=True)
def _no_real_tradingview(monkeypatch):
    """Tests never open a TradingView session: run.py's client would use the owner's real
    sign-in (~/.ta-screener/tv_tokens.json) and could refresh it (2026-09-29: the nightly
    tick's index snapshot tried, and a test waited 145 s on the rate limit). A test that
    needs a client patches run.make_tradingview itself, or points token_path at a temp file."""
    from pathlib import Path

    import run
    from tascreen.errors import ConfigError

    real, owners = run.make_tradingview, Path("~/.ta-screener").expanduser().resolve()

    def guarded(settings, interactive=False):
        if owners in Path(settings.tradingview.token_path).expanduser().resolve().parents:
            raise ConfigError("TradingView is not available in tests (the owner's sign-in)")
        return real(settings, interactive)

    monkeypatch.setattr(run, "make_tradingview", guarded)


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """run.main() installs console handlers bound to the test's captured stderr;
    once that capture closes, later tests would log into a closed stream."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)
