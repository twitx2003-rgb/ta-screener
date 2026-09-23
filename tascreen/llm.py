"""Claude for the channel agents, through the owner's Claude Code subscription.

Adapted from market-research-pipeline's pipeline/debate.py (ClaudeCodeLLM), where
the owner chose the subscription over per-call API billing. Checked against the
installed CLI 2.1.280 (`claude --help`):

    claude -p --output-format json --json-schema <schema> --tools "" --model <m>
           --effort low|medium|high|xhigh|max --system-prompt <text>
           --no-session-persistence --strict-mcp-config

- stdout is one JSON object {type: "result", subtype: "success" | "error_*",
  is_error, result, structured_output, usage, modelUsage, total_cost_usd, ...}; the
  binary carries `structured_output` and `error_max_structured_output_retries`, the
  same shape market-research-pipeline read from 2.1.71's cli.js.
- Billing trap: with ANTHROPIC_API_KEY in the environment the CLI bills the API,
  so the child gets an environment without it. `--bare` is not used for the same
  reason (it accepts only an API key).
- It refuses to start inside a Claude Code session (CLAUDECODE=1); that is
  reported, never bypassed: run from a normal terminal.
- npm's claude.cmd goes through cmd.exe, whose quoting mangles JSON arguments. 2.1.280
  ships a native bin/claude.exe next to it, which is run directly (older installs:
  cli.js with node).
- It runs in an empty temporary folder so no project CLAUDE.md is loaded, with no
  built-in tools and no MCP servers.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Protocol

from .errors import ConfigError, ProviderError


class LLM(Protocol):
    name: str

    def complete(self, *, system: str, user: str, schema: dict) -> tuple[dict, dict]:
        """(parsed JSON, usage dict)."""


class ClaudeCodeLLM:
    CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    EFFORTS = ("low", "medium", "high", "xhigh", "max")

    def __init__(self, *, model: str, effort: str, timeout_s: int = 600,
                 command: list[str] | None = None, run=None, environ: dict | None = None):
        env = os.environ if environ is None else environ
        if env.get("CLAUDECODE") == "1":
            raise ConfigError("the channel agents use Claude Code, which will not start inside "
                              "another Claude Code session. Run this in a normal terminal "
                              "(VS Code: Terminal > New Terminal).")
        if effort not in self.EFFORTS:
            raise ConfigError(f"channels.effort '{effort}' is not one of {', '.join(self.EFFORTS)}")
        self.command = command or self.find_cli()
        self.name = f"claude-code:{model}"
        self.model, self.effort, self.timeout_s = model, effort, timeout_s
        self._run = run or subprocess.run
        self._environ = env

    @staticmethod
    def find_cli() -> list[str]:
        found = shutil.which("claude")
        if not found:
            raise ConfigError("Claude Code (the `claude` command) is not installed or not on PATH")
        package = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code"
        native = package / "bin" / "claude.exe"
        if native.exists():
            return [str(native)]
        cli_js = package / "cli.js"
        if cli_js.exists() and shutil.which("node"):
            return [shutil.which("node"), str(cli_js)]
        if Path(found).suffix.lower() in (".cmd", ".ps1", ".bat"):
            raise ConfigError(f"found {found} but neither {native} nor node + {cli_js}; "
                              "a .cmd shim would mangle the JSON arguments")
        return [found]

    def complete(self, *, system: str, user: str, schema: dict) -> tuple[dict, dict]:
        args = [*self.command, "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
                "--tools", "", "--strict-mcp-config", "--no-session-persistence",
                "--model", self.model, "--effort", self.effort, "--system-prompt", system]
        env = {k: v for k, v in self._environ.items() if k not in self.CREDENTIAL_VARS}
        with tempfile.TemporaryDirectory(prefix="ta-channels-") as cwd:
            try:
                done = self._run(args, input=user.encode("utf-8"), capture_output=True, cwd=cwd,
                                 env=env, timeout=self.timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise ProviderError(f"Claude Code did not answer within {self.timeout_s}s") from exc
        out = done.stdout.decode("utf-8", errors="replace").strip()
        err = done.stderr.decode("utf-8", errors="replace").strip()
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            raise ProviderError(f"Claude Code (exit {done.returncode}) did not return JSON: "
                                f"{(err or out)[:500]}") from None
        if not isinstance(result, dict) or result.get("type") != "result":
            raise ProviderError(f"Claude Code returned an unexpected message: {str(result)[:300]}")
        if result.get("is_error") or result.get("subtype") != "success":
            detail = result.get("errors") or result.get("result") or err
            raise ProviderError(f"Claude Code failed ({result.get('subtype')}): {str(detail)[:500]}")
        parsed = result.get("structured_output")
        if not isinstance(parsed, dict):
            raise ProviderError(f"Claude Code answered without structured output (keys: {sorted(result)})")
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        return parsed, {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "served_by": ",".join(sorted(result.get("modelUsage") or {})) or self.model,
            # What the same tokens would cost on the API; not billed on a subscription.
            "api_equivalent_cost_usd": result.get("total_cost_usd"),
        }


class SyntheticLLM:
    """Offline stand-in for tests: `answer(system, user, schema)` builds the reply."""

    name = "synthetic"

    def __init__(self, answer: Callable[[str, str, dict], dict]):
        self.answer = answer
        self.calls: list[str] = []

    def complete(self, *, system: str, user: str, schema: dict) -> tuple[dict, dict]:
        self.calls.append(user)
        return self.answer(system, user, schema), {"input_tokens": 0, "output_tokens": 0,
                                                    "served_by": "synthetic"}
