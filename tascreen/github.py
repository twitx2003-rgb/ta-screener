"""Starting a workflow of this repository through GitHub's API (workflow_dispatch).

Used by the evening breakout report to request full analyses (analyst.yml) and by the
live watch to continue itself past a runner's 6-hour limit (live.yml). The key is
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
