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

    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GH_DISPATCH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tascreen.notify, "CREDENTIALS",
                        tmp_path_factory.getbasetemp() / "no-telegram.json")


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
