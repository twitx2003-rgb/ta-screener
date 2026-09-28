"""The settings the GitHub runners write (config.local.yaml in .github/*.sh) must load:
on 2026-09-28 state.sh still wrote the removed site's `web` section, and every run that
restores the state stopped at load_settings."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tascreen.config import load_settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("script", ["state.sh", "analyst.sh"])
def test_the_runners_local_settings_load(script, tmp_path):
    text = (ROOT / ".github" / script).read_text(encoding="utf-8")
    block = re.search(r"cat > config\.local\.yaml <<EOF\n(.*?)\nEOF", text, re.S)
    assert block, script
    (tmp_path / "config.yaml").write_text((ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text(block.group(1).replace("$PWD", tmp_path.as_posix()), encoding="utf-8")
    load_settings(tmp_path / "config.yaml", root=tmp_path)
