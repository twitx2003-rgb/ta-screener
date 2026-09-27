"""What is deployed to Vercel since the website was removed: the bot's webhook only."""
from __future__ import annotations

import json

from tascreen.web.export import WEB_DIR, export_webhook


def test_only_the_webhook_is_deployed(tmp_path):
    out = tmp_path / "deploy"
    (out / "old").mkdir(parents=True)
    (out / "old" / "page.html").write_text("stale", encoding="utf-8")
    assert export_webhook(out) == {"files": 3}
    assert not (out / "old").exists()
    assert (out / "api" / "telegram.js").read_bytes() == (WEB_DIR / "vercel" / "telegram.js").read_bytes()
    assert "אין כאן אתר" in (out / "index.html").read_text(encoding="utf-8")
    headers = json.loads((out / "vercel.json").read_text(encoding="utf-8"))["headers"][0]["headers"]
    assert {"key": "X-Content-Type-Options", "value": "nosniff"} in headers
