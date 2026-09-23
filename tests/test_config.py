from __future__ import annotations

import pytest

from tascreen.config import ROOT, load_settings
from tascreen.errors import ConfigError


def _load(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return load_settings(path, root=tmp_path)


def test_the_shipped_config_loads():
    settings = load_settings(ROOT / "config.yaml", root=ROOT)
    assert settings.tradingview.callback_port == 8766            # 8765 is the other project's
    assert settings.tradingview.token_path.startswith("~")        # outside the repo
    assert settings.data_dir == ROOT / "data"


def test_unknown_key_in_a_section_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"unknown key\(s\) under 'tradingview': \['callbak_port'\]"):
        _load(tmp_path, "tradingview:\n  callbak_port: 1\n")


def test_unknown_section_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown section"):
        _load(tmp_path, "tradingviw:\n  url: x\n")


def test_ip_literal_callback_host_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="localhost"):
        _load(tmp_path, "tradingview:\n  callback_host: 127.0.0.1\n")


@pytest.mark.parametrize("value", ["4pm", "25:00", "16"])
def test_bad_session_close_is_refused(tmp_path, value):
    with pytest.raises(ConfigError, match="session_close"):
        _load(tmp_path, f"market:\n  session_close: '{value}'\n")


def test_rate_limit_delays_become_a_tuple_of_floats(tmp_path):
    settings = _load(tmp_path, "tradingview:\n  rate_limit_delays: [1, 2]\n")
    assert settings.tradingview.rate_limit_delays == (1.0, 2.0)
