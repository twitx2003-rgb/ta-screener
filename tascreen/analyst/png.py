"""The analyst's SVG chart as a PNG (Telegram does not show SVG).

On a Linux runner: `rsvg-convert` (librsvg), with the site's fonts installed. On the
owner's Windows computer: Edge (or Chrome) headless takes a screenshot of the SVG, in a
throwaway profile so it never touches the owner's browser.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..errors import ProviderError
from .chart import H, W

SCALE = 2                     # a phone screen shows the double-size image sharply
BROWSERS = ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome")
WINDOWS_BROWSERS = (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")


def _browser() -> str | None:
    for name in BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        for rel in WINDOWS_BROWSERS:
            if root and (Path(root) / rel).exists():
                return str(Path(root) / rel)
    return None


def svg_to_png(svg: Path, png: Path, *, timeout_s: int = 60) -> Path:
    png.unlink(missing_ok=True)
    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        args = [rsvg, "--zoom", str(SCALE), "--output", str(png), str(svg)]
        subprocess.run(args, capture_output=True, timeout=timeout_s)
    else:
        browser = _browser()
        if browser is None:
            raise ProviderError("no rsvg-convert and no Edge/Chrome to turn the chart into a PNG")
        # a fresh profile per shot: a reused one silently skipped shots (CLAUDE.md, phase 3)
        with tempfile.TemporaryDirectory(prefix="ta-shot-", ignore_cleanup_errors=True) as profile:
            args = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--user-data-dir={profile}", f"--window-size={W},{H}",
                    f"--force-device-scale-factor={SCALE}", f"--screenshot={png.resolve()}",
                    svg.resolve().as_uri()]
            subprocess.run(args, capture_output=True, timeout=timeout_s)
    if not png.exists() or png.stat().st_size == 0:
        raise ProviderError(f"could not turn {svg.name} into a PNG")
    return png
