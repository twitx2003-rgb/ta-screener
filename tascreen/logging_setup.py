"""Logging: readable on the console, complete in logs/."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONSOLE_FMT = "%(levelname)-8s %(name)-24s %(message)s"
_FILE_FMT = "%(asctime)s %(levelname)-8s %(name)-24s %(message)s"


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):  # idempotent across repeated calls
        root.removeHandler(handler)

    # Windows consoles may default to a legacy code page, which turns any Hebrew
    # line into UnicodeEncodeError.
    for stream in (sys.stderr, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(_CONSOLE_FMT))
    root.addHandler(console)

    logfile = RotatingFileHandler(log_dir / "screener.log", maxBytes=5_000_000, backupCount=5,
                                  encoding="utf-8")
    logfile.setLevel(logging.DEBUG)
    logfile.setFormatter(logging.Formatter(_FILE_FMT))
    root.addHandler(logfile)

    for noisy in ("httpx", "httpx2", "httpcore", "mcp.client.streamable_http"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
