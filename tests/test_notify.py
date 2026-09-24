"""Telegram messages to the owner: the bot client (a fake API) and the run summaries."""
from __future__ import annotations

import pytest

from tascreen.errors import ProviderError
from tascreen.notify import Telegram, redact, run_message

TOKEN = "123456789:AAH-fake-token-for-tests-only_xyz"


class FakeApi:
    def __init__(self, updates=(), ok=True):
        self.updates, self.ok, self.sent = list(updates), ok, []

    def __call__(self, method, params):
        if not self.ok:
            return {"ok": False, "description": f"Unauthorized for {TOKEN}"}
        if method == "getMe":
            return {"ok": True, "result": {"username": "ta_alerts_bot"}}
        if method == "getUpdates":
            return {"ok": True, "result": self.updates}
        if method == "sendMessage":
            self.sent.append(params)
            return {"ok": True, "result": {}}
        raise AssertionError(method)


def test_the_owner_chat_is_found_and_messaged():
    api = FakeApi(updates=[{"message": {"chat": {"id": -5, "type": "group"}}},
                           {"message": {"chat": {"id": 4242, "type": "private"}}}])
    bot = Telegram(TOKEN, post=api)
    assert bot.bot_name() == "ta_alerts_bot"
    bot.chat_id = bot.find_private_chat(wait_s=0)
    bot.send("שלום")
    assert bot.chat_id == 4242 and api.sent == [{"chat_id": 4242, "text": "שלום",
                                                  "disable_web_page_preview": "true"}]
    assert Telegram(TOKEN, post=FakeApi()).find_private_chat(wait_s=0, sleep=lambda s: None) is None


def test_errors_never_carry_the_token():
    with pytest.raises(ProviderError) as info:
        Telegram(TOKEN, post=FakeApi(ok=False)).bot_name()
    assert TOKEN not in str(info.value) and "<token>" in str(info.value)
    assert redact(f"https://api.telegram.org/bot{TOKEN}/sendMessage") == \
        "https://api.telegram.org/bot<token>/sendMessage"


def test_run_messages():
    done = {"session": "2026-09-23", "due": True, "complete": True,
            "scan": {"symbols_scanned": 2352, "detections": 2600},
            "channels": {"written": 9, "already": 0, "failed": 0, "skipped": 0}}
    text = run_message(done, "success", site_url="https://ta-screener.vercel.app")
    assert text.startswith("✅ העדכון היומי ל-23/09/2026 הושלם.")
    assert "2352 מניות" in text and "9 נכתבו" in text and "vercel.app" in text
    partial = {"session": "2026-09-23", "due": True, "complete": False,
               "bars": {"updated": 2000, "deferred": 300, "failed": 52}}
    assert "חסרות 352 מניות" in run_message(partial, "success")
    assert run_message({"session": "2026-09-23", "due": False}, "success") is None
    assert run_message({}, "failure", run_url="https://github.com/x/runs/1").startswith("❌")
    failed = run_message({"session": "2026-09-23", "due": True, "update_error": "ProviderError"},
                         "failure", run_url="https://github.com/x/runs/1")
    assert "ProviderError" in failed and "https://github.com/x/runs/1" in failed
