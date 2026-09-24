"""Telegram messages to the owner (the "guardian" of the professional-agents plan).

A private bot (made by the owner with @BotFather) sends short Hebrew messages: after
each daily run on GitHub Actions, what was done or what is stuck. Messages carry dates,
counts and links only, never market data.

The bot's token is a secret: it comes from the environment (TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID, GitHub secrets on Actions) or ~/.ta-screener/telegram.json on the
owner's computer, and it is never printed or logged: Telegram's API puts it in the URL,
so every error message is scrubbed with `redact` first.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .errors import ProviderError

API = "https://api.telegram.org"
CREDENTIALS = Path("~/.ta-screener/telegram.json")
_TOKEN = re.compile(r"\d{5,}:[\w-]{20,}")


def redact(text: str) -> str:
    return _TOKEN.sub("<token>", str(text))


class Telegram:
    def __init__(self, token: str, chat_id: str | int | None = None, *,
                 post: Callable[[str, dict], dict] | None = None):
        self.token, self.chat_id = token.strip(), chat_id
        self._post = post or self._http

    def _http(self, method: str, params: dict) -> dict:
        data = urllib.parse.urlencode(params).encode()
        request = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=data)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except ValueError:
                body = {}
            raise ProviderError(redact(f"Telegram {method}: HTTP {exc.code} "
                                       f"{body.get('description', '')}")) from None
        except (OSError, ValueError) as exc:
            raise ProviderError(redact(f"Telegram {method}: {type(exc).__name__}: {exc}")) from None

    def call(self, method: str, **params: Any) -> Any:
        answer = self._post(method, params)
        if not answer.get("ok"):
            raise ProviderError(redact(f"Telegram {method}: {answer.get('description', 'not ok')}"))
        return answer.get("result")

    def bot_name(self) -> str:
        return self.call("getMe").get("username", "")

    def find_private_chat(self, wait_s: float = 120, sleep: Callable[[float], None] = time.sleep) -> int | None:
        """The newest private chat that wrote to the bot (the owner's), waiting up to `wait_s`."""
        deadline = time.monotonic() + wait_s
        while True:
            for update in reversed(self.call("getUpdates", timeout=0) or []):
                chat = (update.get("message") or {}).get("chat") or {}
                if chat.get("type") == "private":
                    return int(chat["id"])
            if time.monotonic() >= deadline:
                return None
            sleep(3)

    def send(self, text: str) -> None:
        if self.chat_id is None:
            raise ProviderError("Telegram: no chat to send to")
        self.call("sendMessage", chat_id=self.chat_id, text=text, disable_web_page_preview="true")


def from_environment() -> Telegram | None:
    """The bot from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, else the owner's saved file."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        return Telegram(token, chat)
    path = CREDENTIALS.expanduser()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        return Telegram(saved["token"], saved["chat_id"])
    except (OSError, ValueError, KeyError):
        return None


def save_credentials(token: str, chat_id: int) -> Path:
    path = CREDENTIALS.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token, "chat_id": chat_id}), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def run_message(summary: dict[str, Any], status: str, run_url: str = "",
                site_url: str = "") -> str | None:
    """The owner's message about one GitHub run (None when there is nothing to say)."""
    session = summary.get("session")
    day = "/".join(reversed(session.split("-"))) if session else "?"
    link = f"\n{run_url}" if run_url else ""
    if status != "success" and not summary:
        return f"❌ הריצה ב-GitHub נכשלה לפני שהתחילה לעבוד. צריך לבדוק.{link}"
    if not summary.get("due"):
        return None if status == "success" else f"❌ הריצה ב-GitHub נכשלה.{link}"
    bars = summary.get("bars") or {}
    missing = int(bars.get("deferred", 0)) + int(bars.get("failed", 0))
    scan = summary.get("scan") or {}
    lines = []
    if summary.get("complete"):
        lines.append(f"✅ העדכון היומי ל-{day} הושלם.")
    elif summary.get("update_error"):
        lines.append(f"❌ העדכון היומי ל-{day} נכשל ({summary['update_error']}). ינסה שוב בריצה הבאה.")
    else:
        lines.append(f"⚠️ העדכון היומי ל-{day} לא הושלם: חסרות {missing} מניות. ימשיך בריצה הבאה.")
    if scan:
        lines.append(f"סריקה: {scan.get('symbols_scanned')} מניות, {scan.get('detections')} זיהויים.")
    channels = summary.get("channels")
    if channels:
        text = f"ערוצים: {channels.get('written', 0)} נכתבו"
        if channels.get("failed"):
            text += f", {channels['failed']} נכשלו"
        if channels.get("skipped"):
            text += f", {channels['skipped']} נדחו (מכסת Claude)"
        lines.append(text + ".")
    elif summary.get("channels_error"):
        lines.append(f"ערוצים: נכשלו ({summary['channels_error']}).")
    if site_url and status == "success":
        lines.append(f"האתר: {site_url}")
    if status != "success":
        lines.append("חלק מהריצה נכשל." + link)
    return "\n".join(lines)
