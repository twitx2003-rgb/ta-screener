from __future__ import annotations

import run


def test_no_command_prints_help_and_exits_2(capsys):
    assert run.main([]) == 2
    assert "--discover" in capsys.readouterr().out


def test_token_status_prints_no_secrets(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(f"tradingview:\n  token_path: '{(tmp_path / 't.json').as_posix()}'\n"
                      f"paths:\n  logs: '{(tmp_path / 'logs').as_posix()}'\n", encoding="utf-8")
    assert run.main(["--config", str(config), "--tradingview-token-status"]) == 0
    out = capsys.readouterr().out
    assert "access_token" in out and "False" in out


def test_unknown_discover_probe_is_a_bad_argument(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"paths:\n  logs: '{(tmp_path / 'logs').as_posix()}'\n", encoding="utf-8")
    assert run.main(["--config", str(config), "--discover", "screener_typo"]) == 2


def test_write_tool_from_the_command_line_is_refused(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(f"paths:\n  logs: '{(tmp_path / 'logs').as_posix()}'\n", encoding="utf-8")
    assert run.main(["--config", str(config), "--tradingview-call", "mcp-tv-create-alert",
                     "symbol=NASDAQ:X"]) == 1
