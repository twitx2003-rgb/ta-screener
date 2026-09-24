"""Tell the GitHub Actions runner to hide the TradingView tokens in this job's log.

Run as its own step command (its stdout is the job log, which the runner reads):
    python .github/mask_tokens.py state/tv_tokens.json
The application never prints token material itself: its output goes to files.
"""
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    tokens = json.loads(path.read_text(encoding="utf-8")).get("tokens") or {}
except (OSError, ValueError):
    tokens = {}
for key in ("access_token", "refresh_token"):
    if tokens.get(key):
        print(f"::add-mask::{tokens[key]}")
print("token file:", "yes" if tokens else "no")
