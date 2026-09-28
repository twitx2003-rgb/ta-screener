"""Starting a workflow of this repository through GitHub's API (workflow_dispatch).

Used by the evening breakout report to request full analyses (analyst.yml), by the
live watch to continue itself past a runner's 6-hour limit (live.yml), and by the X news
loop to continue itself and to start the live watch when GitHub's schedule did not
(2026-09-28: no scheduled run fired for two days). The key is
GH_DISPATCH_TOKEN: a fine-grained key limited to this repository, Actions read/write.
It is sent in a header only and never printed.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
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


def active_runs(workflow: str, *, token: str | None = None, repo: str = REPO,
                get: Callable[[str, dict[str, str]], dict | None] = _get) -> int | None:
    """How many runs of `workflow` are queued, waiting or in progress; None if unknown."""
    token = (token if token is not None else os.environ.get("GH_DISPATCH_TOKEN", "")).strip()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ta-screener"}
    total = 0
    for status in ("queued", "waiting", "pending", "in_progress"):
        answer = get(f"{API}/repos/{repo}/actions/workflows/{workflow}/runs?status={status}&per_page=5",
                     headers)
        if not isinstance(answer, dict) or not isinstance(answer.get("total_count"), int):
            return None
        total += answer["total_count"]
    return total


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
