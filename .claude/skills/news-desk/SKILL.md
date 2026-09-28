---
name: news-desk
description: The owner's X news desk in ta-screener (tascreen/xnews.py, xnews.yml, tascreen/agents/news-*.md, chart-reader.md, market-explainer.md) - which accounts are read, what counts as news, how a Telegram news item must look (short line, short analysis, chart explained, right-aligned Hebrew), the weekend rule, the cost and Claude caps, and how to test a change. Use it for any request about the news, the news messages, the accounts, the costs of twitterapi.io, or the news agents.
---

# The news desk

Talk to the owner in simple Hebrew. Code, comments and commits stay in English.

## What the owner asked for (keep all of it; each came from a request)

- **Source:** the X accounts in `config.yaml` → `xnews.accounts` (63, chosen by the owner
  2026-09-27), read through twitterapi.io (paid per post; key `X_API_KEY` /
  `~/.ta-screener/x.json`, never printed). StockTwits was checked and is blocked.
- **What is news (2026-09-28):** not only the dramatic. Rating 1-5 by the
  `news-screener` role; sent from 3 up (`xnews.min_importance`): 🔴 5 market-moving now,
  🟠 4 relevant to specific stocks or sectors today, 🔵 3 a useful Wall Street update.
- **Few, picked with tweezers (2026-09-28, after 29 stories in 90 minutes):** a strict
  scale in `news-screener.md` (routine commentary, generic charts and sentiment are 2),
  and `xnews.ration`: at most `max_per_round` (2) a round, the most important first (more
  sources rank higher), and `daily_max` (12) regular stories a day from a budget that
  refills through the 20 news hours (about one every 100 minutes). A 5 always goes.
- **The reader's balance (2026-09-28):** once a week `xnews.check_balance` reads
  `/oapi/my/info` (100,000 credits = 1 USD); below `low_balance_usd` (2) the bot warns
  with a top-up link. The amount goes to Telegram only, never to the public log.
- **Weekend (Saturday, Sunday in New York):** only 4-5, dramatic or what matters for the
  coming week (`weekend_min_importance`, `WEEKEND_NOTE`).
- **Hours:** 07:00-03:00 Israel time (04-24 UTC); a pass every 10 minutes on weekdays,
  20 on weekends. After a gap only the last hour is read (`MAX_LOOKBACK_S`): no morning
  flood.
- **The message:** one short Hebrew sentence (about 15 words), then 💡 a short analysis
  (1-2 sentences: why it matters, what is exposed; explain, never forecast or advise),
  then under a picture 📊 one sentence on what the chart shows. Link ↗ to the post.
  Every line right-aligned (`notify.rtl`, applied inside `Telegram`).
- **Pictures:** a picked post's photo goes as the Telegram photo; charts are explained by
  the `chart-reader` role; non-charts get no 📊 line.
- **Costs the owner fixed (2026-09-28):** twitterapi.io about 5-7 USD a month
  (`daily_read_cap: 2000` posts); Claude at most 45 calls a day, judged every 30
  minutes (`claude_daily_cap`, `claude_every_minutes`). Do not raise these without asking.
- **End date:** `xnews.until` (three months, 2026-12-27), then one notice.

## How it runs

- `xnews.yml` is one looping run (5.5 h, then it dispatches its continuation), because
  GitHub's cron dropped every scheduled run for two days. Hourly crons only revive it.
- Each pass also runs `--ensure-live` (starts `live.yml` on a trading day if missing).
- State (seen ids, pending posts, caps) lives on the PRIVATE state repo's `xnews` branch.
- The public Actions log shows counts only, never a post.

## Roles (tascreen/agents/)

| file | does |
|---|---|
| `news-screener.md` (+ `news-deduper.md`) | picks, rates, summarises, analyses; merges one story told by several accounts and drops repeats of what was sent |
| `chart-reader.md` | one sentence per chart picture |
| `market-explainer.md` | why the market moves (pre-market reports, sharp index moves) |

Change a role by editing its file, not the code. Keep the front matter (`name`, `team`,
`does`, `used_by`); `tests/test_agents.py` checks it.

## Checking a change

1. `.venv\Scripts\python.exe -m pytest -q` (offline; `SyntheticLLM`, fake reader).
2. Field changes on the X side: `run.py --x-discover <account>` (keys only).
3. Claude cannot run inside Claude Code: a real pass runs in GitHub
   (dispatch `xnews.yml`) or in the owner's normal terminal (`run.py --xnews`).
4. The run's status line: `xnews: read=… new=… judged=… sent=… photos=…`.
