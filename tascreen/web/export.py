"""What is deployed to Vercel: only the Telegram bot's webhook (the owner removed the
website, 2026-09-27, and kept Telegram).

    api/telegram.js   the webhook function (copied from web/vercel/): a symbol written
                      to the bot starts the analyst workflow
    index.html        one line: there is no site here
    vercel.json       security headers

The files do not depend on the data, so every deployment is the same few bytes.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

WEB_DIR = Path(__file__).resolve().parent

VERCEL_CONFIG = {
    "headers": [
        {"source": "/(.*)", "headers": [
            {"key": "X-Content-Type-Options", "value": "nosniff"},
            {"key": "Referrer-Policy", "value": "no-referrer"},
            {"key": "Content-Security-Policy", "value": "default-src 'none'; frame-ancestors 'none'"}]},
    ],
}

INDEX = ('<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8">'
         '<title>ta-screener</title><p>אין כאן אתר. הסורק עובד דרך בוט הטלגרם.</p></html>\n')


def export_webhook(out: Path) -> dict[str, Any]:
    """Write the deployment to `out` (replaced). Returns the file count."""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "api").mkdir(parents=True)
    shutil.copyfile(WEB_DIR / "vercel" / "telegram.js", out / "api" / "telegram.js")
    (out / "index.html").write_text(INDEX, encoding="utf-8")
    (out / "vercel.json").write_text(json.dumps(VERCEL_CONFIG, indent=2), encoding="utf-8")
    return {"files": sum(1 for p in out.rglob("*") if p.is_file())}
