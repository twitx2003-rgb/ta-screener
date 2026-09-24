"""Copy the home computer's history into the private state repository (stage 3, once).

The runners start with their own data, but two things only the home computer has:
- the outcome ledger with the backfill (data/outcomes/ledger.parquet + meta.json);
- the channels' posts and chart images so far (data/channels/).
Run it when no GitHub run is in progress (a run's final save would overwrite it):

    .venv\\Scripts\\python.exe deploy\\seed_state.py

It clones twitx2003-rgb/ta-screener-state with your git credentials into a temporary
folder, copies those files over, commits and pushes. Nothing else is touched: the
runners' TradingView sign-in, bars and scans stay theirs.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = "https://github.com/twitx2003-rgb/ta-screener-state.git"


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


def main() -> int:
    data = ROOT / "data"
    ledger = data / "outcomes" / "ledger.parquet"
    if not ledger.exists():
        print("no ledger in data/outcomes: nothing to seed")
        return 1
    with tempfile.TemporaryDirectory(prefix="ta-state-") as tmp:
        work = Path(tmp) / "state"
        git("clone", "-q", STATE, str(work))
        for name in ("ledger.parquet", "meta.json"):
            target = work / "data" / "outcomes" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(data / "outcomes" / name, target)
        if (data / "channels").exists():
            shutil.copytree(data / "channels", work / "data" / "channels", dirs_exist_ok=True)
        git("add", "data/outcomes/ledger.parquet", "data/outcomes/meta.json", "data/channels",
            cwd=work)
        git("-c", "user.name=ta-screener owner", "-c", "user.email=actions@users.noreply.github.com",
            "commit", "-q", "-m", "Seed from the home computer: outcome ledger and channels", cwd=work)
        git("push", "-q", "origin", "HEAD:main", cwd=work)
        files = git("show", "--stat", "--oneline", "HEAD", cwd=work).strip().splitlines()
        print(f"pushed: {files[0]}\n{files[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
