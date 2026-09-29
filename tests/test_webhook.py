"""The Telegram webhook function (web/vercel/telegram.js), run under Node with fake
GitHub and Telegram endpoints, and the Python side that points the bot at it."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tascreen.notify import Telegram, webhook_secret

FUNCTION = Path(__file__).resolve().parents[1] / "tascreen" / "web" / "vercel" / "telegram.js"
TOKEN = "123456:" + "x" * 30

HARNESS = r"""
const fn = require(process.argv[2]);
const token = process.argv[3], owner = "42";
const env = {TELEGRAM_BOT_TOKEN: token, TELEGRAM_CHAT_ID: owner, TELEGRAM_GROUP_ID: "-555",
             GH_DISPATCH_TOKEN: "dispatch-key"};
let calls = [], dispatchStatus = 204;
global.fetch = async (url, opts) => {
  calls.push({url, body: JSON.parse(opts.body), auth: (opts.headers || {}).Authorization || null});
  return {status: url.includes("api.github.com") ? dispatchStatus : 200};
};
const update = (text, chat = 42, from = 42) => ({message: {chat: {id: chat}, from: {id: from}, text}});
const good = {"x-telegram-bot-api-secret-token": fn.webhookSecret(token)};
async function run(name, req) { calls = []; return {name, outcome: await fn.handle(req, env), calls}; }
(async () => {
  const out = [];
  out.push(await run("no secret", {method: "POST", headers: {}, body: update("NVDA")}));
  out.push(await run("wrong secret", {method: "POST",
    headers: {"x-telegram-bot-api-secret-token": "0".repeat(64)}, body: update("NVDA")}));
  out.push(await run("stranger", {method: "POST", headers: good, body: update("NVDA", 7, 7)}));
  out.push(await run("group", {method: "POST", headers: good, body: update("NVDA", -100, 42)}));
  out.push(await run("member asks", {method: "POST", headers: good, body: update(" $amd ", -555, 7)}));
  out.push(await run("members talk", {method: "POST", headers: good, body: update("OK", -555, 7)}));
  out.push(await run("group help", {method: "POST", headers: good,
                                    body: update("/start@ta_screener_alert_bot", -555, 7)}));
  out.push(await run("a bot in the group", {method: "POST", headers: good,
    body: {message: {chat: {id: -555}, from: {id: 9, is_bot: true}, text: "$NVDA"}}}));
  out.push(await run("get", {method: "GET", headers: good, body: update("NVDA")}));
  out.push(await run("not a symbol", {method: "POST", headers: good, body: update("rm -rf /")}));
  out.push(await run("symbol", {method: "POST", headers: good, body: update(" $nvda ")}));
  calls = [];
  const padded = {TELEGRAM_BOT_TOKEN: token + "\n", TELEGRAM_CHAT_ID: " 42\n", GH_DISPATCH_TOKEN: "dispatch-key\n"};
  out.push({name: "padded keys", outcome: await fn.handle(
    {method: "POST", headers: good, body: update("NVDA")}, padded), calls});
  out.push({name: "status", full: fn.status(env), empty: fn.status({})});
  dispatchStatus = 401;
  out.push(await run("refused", {method: "POST", headers: good, body: update("NASDAQ:NVDA")}));
  global.fetch = async () => { throw new Error("network down"); };
  process.env.TELEGRAM_BOT_TOKEN = token; process.env.TELEGRAM_CHAT_ID = owner;
  let status = null;
  await fn({method: "POST", headers: good, body: update("NVDA")},
           {status(s) { status = s; return {json() {}}; }});
  out.push({name: "thrown", status});
  console.log("RESULT " + JSON.stringify({secret: fn.webhookSecret(token), out}));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_webhook_answers_only_the_owner_and_only_symbols(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    done = subprocess.run([shutil.which("node"), str(harness), str(FUNCTION), TOKEN],
                          capture_output=True, timeout=60)
    line = [l for l in done.stdout.decode("utf-8").splitlines() if l.startswith("RESULT ")]
    assert line, done.stderr.decode("utf-8", errors="replace")[-500:]
    result = json.loads(line[-1][len("RESULT "):])
    assert result["secret"] == webhook_secret(TOKEN)            # Python and Node agree
    by = {o["name"]: o for o in result["out"]}
    for name, outcome in (("no secret", "bad secret"), ("wrong secret", "bad secret"),
                          ("stranger", "not the owner"), ("group", "not the owner"),
                          ("get", "not set up"), ("members talk", "group conversation"),
                          ("a bot in the group", "group: a bot")):
        assert by[name]["outcome"] == outcome and not by[name]["calls"], name
    help_calls = by["not a symbol"]["calls"]
    assert by["not a symbol"]["outcome"] == "help" and len(help_calls) == 1
    assert "api.telegram.org" in help_calls[0]["url"] and "NVDA" in help_calls[0]["body"]["text"]
    dispatch, told = by["symbol"]["calls"]
    assert dispatch["url"].endswith("/actions/workflows/analyst.yml/dispatches")
    assert dispatch["body"] == {"ref": "main", "inputs": {"symbol": "NVDA", "chat": "42"}}
    assert dispatch["auth"] == "Bearer dispatch-key" and "dispatch-key" not in told["url"]
    assert told["body"]["chat_id"] == "42" and "מנתח את NVDA" in told["body"]["text"]
    assert by["padded keys"]["outcome"] == "started"            # a pasted newline is harmless
    assert by["padded keys"]["calls"][0]["auth"] == "Bearer dispatch-key"
    assert by["status"]["full"] == {"ok": True, "bot": True, "owner": True, "group": True, "dispatch": True}
    assert by["status"]["empty"] == {"ok": True, "bot": False, "owner": False, "group": False, "dispatch": False}
    # the owner's group: any member, with a cashtag only; the answer goes to the group
    asked, said = by["member asks"]["calls"]
    assert by["member asks"]["outcome"] == "started"
    assert asked["body"]["inputs"] == {"symbol": "AMD", "chat": "-555"} and said["body"]["chat_id"] == "-555"
    assert by["group help"]["outcome"] == "group help" and len(by["group help"]["calls"]) == 1
    assert by["group help"]["calls"][0]["body"]["chat_id"] == "-555" and "$NVDA" in by["group help"]["calls"][0]["body"]["text"]
    assert by["refused"]["outcome"] == "dispatch failed"
    assert "GitHub 401" in by["refused"]["calls"][1]["body"]["text"]
    assert by["thrown"]["status"] == 200                         # Telegram never retries


def test_the_bot_is_pointed_at_the_webhook_with_the_derived_secret():
    sent = []
    bot = Telegram(TOKEN, 42, post=lambda m, p: sent.append((m, p)) or {"ok": True, "result": {}})
    bot.set_webhook("https://site.example/api/telegram/")
    method, params = sent[0]
    assert method == "setWebhook" and params["url"] == "https://site.example/api/telegram/"
    assert params["secret_token"] == webhook_secret(TOKEN) and len(params["secret_token"]) == 64
    assert json.loads(params["allowed_updates"]) == ["message"]
    assert webhook_secret(TOKEN) != webhook_secret(TOKEN[:-1] + "y")


def test_the_bot_sends_to_the_group_and_answers_where_it_was_asked(tmp_path, monkeypatch):
    import run
    import tascreen.analyst.request as request
    from tascreen import notify
    from tascreen.config import Settings

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.delenv("TELEGRAM_GROUP_ID", raising=False)
    assert notify.from_environment().chat_id == "42"
    monkeypatch.setenv("TELEGRAM_GROUP_ID", " -555\n")
    assert notify.from_environment().chat_id == "-555"               # everything goes to the group
    answered = []
    monkeypatch.setattr(request, "handle_request", lambda text, **kw: answered.append(kw["bot"].chat_id) or "sent")
    settings = Settings(root=tmp_path)
    for reply in ("", "42", "-555", "-999"):
        assert run.ci_analyze(settings, "NVDA", str(tmp_path / "kept"), 10, reply) == 0
    assert answered == ["-555", "42", "-555", "-555"]                  # an unknown chat: the group
