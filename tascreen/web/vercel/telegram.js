// The Telegram bot's webhook, a Vercel function (chart analyst, stage T5, 2026-09-24).
// tascreen/web/export.py copies this file to api/telegram.js, the only thing deployed.
//
// The owner writes a symbol to the bot; Telegram posts the update here; this starts the
// analyst workflow on GitHub (which answers in the same chat within a minute) and marks
// the request with a 👀 reaction instead of a message (owner, 2026-09-29: no extra text).
// Guards: Telegram's secret header (an HMAC of the bot token: no extra secret to keep),
// the owner's private chat, or the owner's group (TELEGRAM_GROUP_ID; owner, 2026-09-29: any
// member may ask there, like in the private chat: a ticker in capitals, "NVDA", or "$nvda";
// the members' own conversation never starts an analysis, and an unknown capital word gets
// no answer), and a symbol pattern only. The analyses have a daily limit. It always answers
// 200, so Telegram never retries an update. The keys come from the deployment's run-time
// environment (vercel deploy -e ...), which webhook.yml and run.yml fill from the secrets.
"use strict";
const crypto = require("crypto");

const SYMBOL = /^\$?(?:[A-Za-z]{2,8}:)?[A-Za-z][A-Za-z0-9.\-]{0,9}$/;
const WORKFLOW = "https://api.github.com/repos/twitx2003-rgb/ta-screener/actions/workflows/analyst.yml/dispatches";
const HELP = "שלחו סימול של מניה, למשל NVDA, ותקבלו ניתוח טכני: גרף והסבר קצר. לא ייעוץ השקעות.";
const GROUP_HELP = "כדי לקבל ניתוח טכני של מניה, כתבו את הסימול באותיות גדולות, למשל NVDA. " +
  "התשובה מגיעה לקבוצה תוך דקה. לא ייעוץ השקעות.";
const COMMAND = /^\/(start|help)(@\w+)?$/i;
// a ticker written alone in the group, in capitals ("NVDA", "NASDAQ:NVDA", "BRK.B")
const BARE = /^(?:[A-Z]{2,8}:)?[A-Z][A-Z0-9.\-]{0,9}$/;
// capital words people write in a chat, not tickers
const CHAT_WORDS = new Set(["OK", "LOL", "WOW", "YES", "NO", "HI", "OMG", "WTF", "BTW", "THX", "TNX", "GM", "GN",
                            "USA", "CEO", "FYI", "ASAP", "IMO", "LMAO", "WAIT"]);
const SEEN = "👀";

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

// the request's "seen" mark; a chat that allows no reactions just gets none
async function react(token, chat, messageId) {
  if (!messageId) return;
  await fetch(`https://api.telegram.org/bot${token}/setMessageReaction`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({chat_id: chat, message_id: messageId, reaction: [{type: "emoji", emoji: SEEN}]}),
  });
}

// The keys as pasted into GitHub's secrets: a trailing newline would change the HMAC.
function keys(env) {
  const clean = (name) => String(env[name] || "").trim();
  return {token: clean("TELEGRAM_BOT_TOKEN"), owner: clean("TELEGRAM_CHAT_ID"),
          group: clean("TELEGRAM_GROUP_ID"), dispatch: clean("GH_DISPATCH_TOKEN")};
}

// GET: which keys this deployment has (yes/no only, never a value), to check a deployment.
function status(env) {
  const k = keys(env);
  return {ok: true, bot: k.token.length > 0, owner: k.owner.length > 0, group: k.group.length > 0,
          dispatch: k.dispatch.length > 0};
}

async function handle(req, env) {
  const {token, owner, group, dispatch} = keys(env);
  if (req.method !== "POST" || !token || !owner) return "not set up";
  if (!same((req.headers || {})["x-telegram-bot-api-secret-token"], webhookSecret(token))) return "bad secret";
  const message = (req.body && req.body.message) || {};
  const chat = String((message.chat || {}).id), from = (message.from || {});
  // direction marks a Hebrew keyboard (or a copied line) puts around a command or symbol
  const text = String(message.text || "").replace(/[\u200E\u200F\u202A-\u202E\u2066-\u2069]/g, "").trim();
  // "$NVDA", "$ NVDA", or the Hebrew keyboard's currency key: the shekel sign
  let asked = text.replace(/^[$₪]\s*/, "");
  let quiet = "";                       // "true": an unknown symbol gets no answer
  if (group && chat === group) {
    if (from.is_bot) return "group: a bot";
    // in the group a request is a ticker in capitals, "$NVDA" or "@bot NVDA"; anything
    // else is the members' talk
    const mention = text.match(/^@\w+bot\s+(\S+)$/i);
    if (mention) asked = mention[1];
    const request = Boolean(mention) || /^[$₪]\s*[A-Za-z]/.test(text) || /^@\w+bot\b/i.test(text);
    if (COMMAND.test(text) || (request && !SYMBOL.test(asked))) {
      await say(token, chat, GROUP_HELP);
      return "group help";
    }
    if (!request) {
      if (!BARE.test(text) || CHAT_WORDS.has(text)) return "group conversation";
      quiet = "true";
    }
  } else if (chat !== owner || String(from.id) !== owner) {
    return "not the owner";
  } else if (!SYMBOL.test(asked)) {
    await say(token, owner, HELP);
    return "help";
  }
  const symbol = asked.toUpperCase();
  const started = await fetch(WORKFLOW, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${dispatch}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "ta-screener-bot",
    },
    body: JSON.stringify({ref: "main", inputs: {symbol, chat, quiet}}),
  });
  if (started.status === 204) {
    await react(token, chat, message.message_id);
    return "started";
  }
  await say(token, chat, `לא הצלחתי להפעיל את הניתוח (GitHub ${started.status}). נסו שוב מאוחר יותר.`);
  return "dispatch failed";
}

module.exports = async function telegram(req, res) {
  if (req.method === "GET") {
    res.status(200).json(status(process.env));
    return;
  }
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
module.exports.status = status;
module.exports.webhookSecret = webhookSecret;
