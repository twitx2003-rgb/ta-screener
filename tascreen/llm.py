"""Claude for the chart analyst and the X news, through the owner's Claude Code subscription.

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

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Protocol

from .errors import ConfigError, ProviderError


# Live (2026-09-23): {"subtype": "success", "is_error": true, "result": "You've hit your
# session limit · resets 11:50pm (Asia/Jerusalem)"}.
LIMIT_TEXT = re.compile(r"hit your (session|usage|weekly|daily) limit|usage limit|rate limit", re.I)


class UsageLimit(ProviderError):
    """The subscription's usage limit: further calls fail until it resets."""


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
            raise ConfigError("this uses Claude Code, which will not start inside "
                              "another Claude Code session. Run this in a normal terminal "
                              "(VS Code: Terminal > New Terminal).")
        if effort not in self.EFFORTS:
            raise ConfigError(f"effort '{effort}' is not one of {', '.join(self.EFFORTS)}")
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

    def _args(self, system: str, schema: dict, output: str) -> list[str]:
        return [*self.command, "-p", "--output-format", output, "--json-schema", json.dumps(schema),
                "--tools", "", "--strict-mcp-config", "--no-session-persistence",
                "--model", self.model, "--effort", self.effort, "--system-prompt", system]

    def _call(self, args: list[str], stdin: bytes):
        env = {k: v for k, v in self._environ.items() if k not in self.CREDENTIAL_VARS}
        with tempfile.TemporaryDirectory(prefix="ta-claude-") as cwd:
            try:
                done = self._run(args, input=stdin, capture_output=True, cwd=cwd,
                                 env=env, timeout=self.timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise ProviderError(f"Claude Code did not answer within {self.timeout_s}s") from exc
        return (done.returncode, done.stdout.decode("utf-8", errors="replace").strip(),
                done.stderr.decode("utf-8", errors="replace").strip())

    def complete(self, *, system: str, user: str, schema: dict) -> tuple[dict, dict]:
        code, out, err = self._call(self._args(system, schema, "json"), user.encode("utf-8"))
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            raise ProviderError(f"Claude Code (exit {code}) did not return JSON: "
                                f"{(err or out)[:500]}") from None
        return self._answer(result, err)

    def complete_with_images(self, *, system: str, user: str, images: list[tuple[str, bytes]],
                             schema: dict) -> tuple[dict, dict]:
        """The same call with pictures: one stream-json user message holding the images
        (base64, `media_type` such as image/jpeg) and then the text. With
        `--input-format stream-json` the CLI answers in stream-json too (one JSON object
        a line; `--verbose` is required for that in -p mode); the last line of type
        "result" has the same fields as the plain JSON answer. (The shape follows the
        CLI's help and the Messages API's image blocks; `run.py --x-vision-check`
        confirms it on a real call.)"""
        content = [{"type": "image", "source": {"type": "base64", "media_type": media,
                                                "data": base64.b64encode(data).decode("ascii")}}
                   for media, data in images]
        content.append({"type": "text", "text": user})
        line = json.dumps({"type": "user", "message": {"role": "user", "content": content}})
        args = [*self._args(system, schema, "stream-json"), "--input-format", "stream-json", "--verbose"]
        code, out, err = self._call(args, (line + "\n").encode("utf-8"))
        result = None
        for raw in out.splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
        if result is None:
            raise ProviderError(f"Claude Code (exit {code}) gave no result line: {(err or out)[-500:]}")
        return self._answer(result, err)

    def _answer(self, result, err: str) -> tuple[dict, dict]:
        if not isinstance(result, dict) or result.get("type") != "result":
            raise ProviderError(f"Claude Code returned an unexpected message: {str(result)[:300]}")
        if result.get("is_error") or result.get("subtype") != "success":
            detail = str(result.get("errors") or result.get("result") or err)[:500]
            if LIMIT_TEXT.search(detail):
                raise UsageLimit(f"Claude usage limit reached: {detail}")
            raise ProviderError(f"Claude Code returned an error ({result.get('subtype')}): {detail}")
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

    def complete_with_images(self, *, system: str, user: str, images: list[tuple[str, bytes]],
                             schema: dict) -> tuple[dict, dict]:
        self.images = getattr(self, "images", []) + [len(images)]
        return self.complete(system=system, user=user, schema=schema)
