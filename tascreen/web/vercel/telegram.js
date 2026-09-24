// The Telegram bot's webhook, a Vercel function (chart analyst, stage T5, 2026-09-24).
// tascreen/web/export.py copies this file to api/telegram.js in every exported site.
//
// The owner writes a symbol to the bot; Telegram posts the update here; this starts the
// analyst workflow on GitHub (which answers in Telegram a few minutes later) and says so.
// Guards: Telegram's secret header (an HMAC of the bot token: no extra secret to keep),
// the owner's chat and user id only, and a symbol pattern only. It always answers 200, so
// Telegram never retries an update. The keys come from the deployment's run-time
// environment (vercel deploy -e ...), which run.yml fills from the GitHub secrets.
"use strict";
const crypto = require("crypto");

const SYMBOL = /^\$?(?:[A-Za-z]{2,8}:)?[A-Za-z][A-Za-z0-9.\-]{0,9}$/;
const WORKFLOW = "https://api.github.com/repos/twitx2003-rgb/ta-screener/actions/workflows/analyst.yml/dispatches";
const HELP = "שלחו סימול של מניה, למשל NVDA, ותקבלו ניתוח טכני: גרף, הסבר קצר וסקריפט ל-TradingView. " +
  "לא ייעוץ השקעות.";

function webhookSecret(botToken) {
  return crypto.createHmac("sha256", botToken).update("ta-screener telegram webhook").digest("hex");
}

function same(a, b) {
  const x = Buffer.from(String(a || "")), y = Buffer.from(String(b || ""));
  return x.length > 0 && x.length === y.length && crypto.timingSafeEqual(x, y);
}

async function say(token, chat, text) {
  await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({chat_id: chat, text}),
  });
}

async function handle(req, env) {
  const token = env.TELEGRAM_BOT_TOKEN, owner = String(env.TELEGRAM_CHAT_ID || "");
  if (req.method !== "POST" || !token || !owner) return "not set up";
  if (!same((req.headers || {})["x-telegram-bot-api-secret-token"], webhookSecret(token))) return "bad secret";
  const message = (req.body && req.body.message) || {};
  if (String((message.chat || {}).id) !== owner || String((message.from || {}).id) !== owner) {
    return "not the owner";
  }
  const text = String(message.text || "").trim();
  if (!SYMBOL.test(text)) {
    await say(token, owner, HELP);
    return "help";
  }
  const symbol = text.replace(/^\$/, "").toUpperCase();
  const started = await fetch(WORKFLOW, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GH_DISPATCH_TOKEN || ""}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "ta-screener-bot",
    },
    body: JSON.stringify({ref: "main", inputs: {symbol}}),
  });
  if (started.status === 204) {
    await say(token, owner, `מנתח את ${symbol}. התשובה תגיע תוך כמה דקות.`);
    return "started";
  }
  await say(token, owner, `לא הצלחתי להפעיל את הניתוח (GitHub ${started.status}). נסו שוב מאוחר יותר.`);
  return "dispatch failed";
}

module.exports = async function telegram(req, res) {
  let outcome = "error";
  try {
    outcome = await handle(req, process.env);
  } catch (err) {
    outcome = `error: ${err && err.name}`;
  }
  console.log(`telegram webhook: ${outcome}`);
  res.status(200).json({ok: true});        // always 200: Telegram must not retry an update
};
module.exports.handle = handle;
module.exports.webhookSecret = webhookSecret;
