"""Starting a workflow of this repository through GitHub's API (workflow_dispatch).

Used by the evening breakout report to request full analyses (analyst.yml), by the
live watch to continue itself past a runner's 6-hour limit (live.yml), and by the X news
loop to continue itself and to start the live watch or the nightly run when GitHub's
schedule did not (2026-09-28: no scheduled run fired for two days; 2026-10-06: one nightly
time of three). The key is
GH_DISPATCH_TOKEN: a fine-grained key limited to this repository, Actions read/write.
It is sent in a header only and never printed.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable

REPO = "twitx2003-rgb/ta-screener"
API = "https://api.github.com"


def _post(url: str, body: bytes, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def _get(url: str, headers: dict[str, str]) -> dict | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ta-screener"}


def active_runs(workflow: str, *, token: str | None = None, repo: str = REPO,
                get: Callable[[str, dict[str, str]], dict | None] = _get) -> int | None:
    """How many runs of `workflow` are queued, waiting or in progress; None if unknown."""
    token = (token if token is not None else os.environ.get("GH_DISPATCH_TOKEN", "")).strip()
    if not token:
        return None
    headers = _headers(token)
    total = 0
    for status in ("queued", "waiting", "pending", "in_progress"):
        answer = get(f"{API}/repos/{repo}/actions/workflows/{workflow}/runs?status={status}&per_page=5",
                     headers)
        if not isinstance(answer, dict) or not isinstance(answer.get("total_count"), int):
            return None
        total += answer["total_count"]
    return total


def runs_since(workflow: str, since: datetime, *, token: str | None = None, repo: str = REPO,
               get: Callable[[str, dict[str, str]], dict | None] = _get) -> list[dict[str, str]] | None:
    """The runs of `workflow` created at or after `since`, newest first, each with its
    `status` and `conclusion` (None while it runs); None if unknown."""
    token = (token if token is not None else os.environ.get("GH_DISPATCH_TOKEN", "")).strip()
    if not token:
        return None
    after = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    answer = get(f"{API}/repos/{repo}/actions/workflows/{workflow}/runs?per_page=20"
                 f"&created={urllib.parse.quote('>=' + after)}", _headers(token))
    if not isinstance(answer, dict) or not isinstance(answer.get("workflow_runs"), list):
        return None
    return [{"status": str(r.get("status")), "conclusion": r.get("conclusion")}
            for r in answer["workflow_runs"] if isinstance(r, dict)]


def dispatch(workflow: str, inputs: dict[str, str], *, token: str | None = None,
             repo: str = REPO, ref: str = "main",
             post: Callable[[str, bytes, dict[str, str]], int] = _post) -> int:
    """Start `workflow` (a file in .github/workflows/). Returns the HTTP status: 204 is
    started; 0 means no key or no connection."""
    token = (token if token is not None else os.environ.get("GH_DISPATCH_TOKEN", "")).strip()
    if not token:
        return 0
    url = f"{API}/repos/{repo}/actions/workflows/{workflow}/dispatches"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ta-screener",
               "Content-Type": "application/json"}
    return post(url, json.dumps({"ref": ref, "inputs": inputs}).encode(), headers)
