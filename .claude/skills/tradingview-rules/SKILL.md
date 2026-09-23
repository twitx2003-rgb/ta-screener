---
name: tradingview-rules
description: Rules for using TradingView's MCP tools in ta-screener — read-only calls only, EXCHANGE:TICKER symbols, the screener's filter syntax and 1000-row cap, 429 rate limits reported inside a "success" payload, the response shapes already mapped, and what must never be committed. Use it before calling any mcp__tradingview__* tool, and before writing or changing code under tascreen/tv/ or anything that reads TradingView data.
---

# TradingView MCP in ta-screener

Answer the user in Hebrew. Code, comments and commit messages stay in English.

## Two ways in — pick the right one

| Need | Use |
|---|---|
| Look at one or a few symbols, check a pattern, answer a question | Claude Code's MCP tools `mcp__tradingview__mcp-tv-*` |
| Anything over ~20 symbols (universe, bars for the scan) | The project's Python client via `run.py` (`tascreen/tv/`) |

Never loop MCP tool calls over hundreds of symbols in the conversation. That is what
`run.py` is for: it keeps one session open, retries 429s, checks every bar and caches
results under `data/`.

## Hard rules

1. **Read only.** Tools named get-, list-, search- and `run-screener` are allowed. The
   same account can create, update, delete, restart and stop alerts, and create, update,
   add to, remove from and delete watchlists. Those 10 tools are denied in
   `.claude/settings.json` and refused by `is_read_only()` in `tascreen/tv/mcp_client.py`.
   Do not try to work around either.
2. **Symbols are `EXCHANGE:TICKER`**, e.g. `NASDAQ:AAPL` or `NYSE:JPM`. If the exchange is
   unknown, call `mcp-tv-search-symbols` (with `type_filter: "stock"`) first. Do not guess
   the exchange.
3. **Check `success`, not just `is_error`.** A failed call comes back with
   `is_error: false` and the payload `{"success": false, "error": "..."}`. An error text
   that contains `429` is a rate limit.
4. **Never guess a field or column name.** Screener columns come from
   `mcp-tv-get-screener-columns` (`market`, `group`, `search`). The catalogue saved by
   `run.py --discover` is in `logs/discover/`; read it before inventing a name. In code,
   read every payload field through `tascreen.fields.pick()`, which raises and lists the
   keys actually present.
5. **No vendor data in the repo.** The repo is public, and TradingView data may not be
   redistributed. Never put prices, volumes, market caps or payload excerpts into
   committed files, commit messages or test fixtures. Tests use synthetic numbers.
   `data/` and `logs/` are gitignored.

## Rate limits (429)

Tools backed by `scanner.tradingview.com` have been rate limited for hours at a time:
`run-screener`, `get-symbol-data`, `get-symbol-data-batch`, `get-earnings-calendar` and
`get-financials`. `get-ohlcv` has not been. When you see a 429:

- wait and retry at most twice, with growing waits (about 15 s, then 45 s);
- if it is still 429, stop and tell the user. Do not keep retrying.

The Python side does the same with `tradingview.rate_limit_delays` in `config.yaml`.

## The screener (`mcp-tv-run-screener`)

- Arguments:
  - `market` (`"america"` for US).
  - `symbol_types` (e.g. `["stock"]`).
  - `filters` as `{column: [min, max]}`, with `null` meaning no bound. There are also the
    special keys `index`, `sector`, `industry` and `analyst_rating`.
  - `sort_by`, `sort_order`, `limit`, `columns`, `filter_preset`, `symbolset`.
- There are **no operators** (such as crosses or equals) beyond the `[min, max]` ranges.
- `limit` is **capped at 1000** and there is **no offset**. To get every US stock above
  $1B (thousands of rows), split `market_cap_basic` into bands. Check that each band
  returned all of its `totalCount`, and that the bands add up to the unsplit total.
- The market-cap column is `market_cap_basic`, in raw USD. That name is confirmed by a
  live `get-symbol-data` answer.

## Response shapes already mapped (live)

- **`get-ohlcv`** (`symbol`, `interval` "1D", `count` ≤ 5000):
  - The payload is `{success, symbol, interval, count, notice, summary, bars: [{t, o, h, l, c, v}]}`.
  - Bars are oldest first. `t` is unix seconds at the session open.
  - **The newest bar can be today's unfinished session.** Before 16:15 New York time,
    today's bar is not a close. `tascreen.market_hours.drop_incomplete_session` drops it.
- **`get-symbol-data`** with `columns: ["close", "market_cap_basic"]` returns
  `{success, data: {close, market_cap_basic}}`.
- **`get-earnings-calendar`** returns `{success, data: {count, from, to, earnings: [{symbol, release_date, release_next_date, ...}]}}`.
  A next date that falls on a weekend is a placeholder estimate, not a confirmed date.
- **`get-screener-columns`** (not rate limited):
  - With no group, it returns `{success, count, hint, groups: [{group, count, columns: [names]}]}`
    across 17 groups.
  - With a group or search, it returns `{success, count, columns: [{name, description, group, markets}]}`.
  - A search with no match is `success: false`, "no columns matched".
- **What the catalogue has, and lacks:**
  - There are **no candlestick, chart-pattern or SMA/EMA columns**. This project computes
    those from bars. Do not use scanner names such as `Candle.*` or `SMA50`; they are
    not in the catalogue.
  - Technicals: `RSI`, `ATRP`, `ADX`, `MACD.macd`, `Stoch`, `BB`, `CCI`, `Mom`, `AO`,
    `Aroon`, `TechRating_1D`, `MARating_1D`, `OsRating_1D`, `AnalystRating`. These are 1D
    by default; other timeframes take a `|1W`-style suffix.
  - Other columns: `exchange`, `country`, `sector`, `average_volume_10d_calc`,
    `relative_volume_10d_calc`, `Value.Traded`.
  - There is **no type or ADR column**.
- **`run-screener`** (live, 2026-09-23):
  - Shape: `{success, data: {rows: [...], totalCount}}`.
  - Default rows carry `symbol` ("EXCHANGE:TICKER"), `name` (ticker), `description`,
    `type`, `subtype`, `sector`, `industry`, `market_cap_basic`, `close`, `volume`,
    `RSI`, `EMA50`, `EMA200`, `Recommend.All`, `BB.*`, `Perf.*`, earnings dates, and a
    few more.
  - There is **no `exchange` key**: the exchange is the symbol prefix.
  - Values can be null.
  - **Results include OTC symbols** (`OTC:...`). There is no filter for that, so drop
    them by prefix.
  - More than $1B is roughly 4,000 rows including OTC, so the 1000-row cap means
    splitting `market_cap_basic` into bands.
- **`get-ohlcv` notice** (live):
  - bars are delayed 15 minutes or more, and the last bar may still change;
  - there are no pre- or post-market bars;
  - prices are **split-adjusted only**.

## When the MCP tools are missing or ask to sign in

- In Claude Code: run `/mcp`, choose `tradingview` and authenticate.
- For the Python client: run `.venv\Scripts\python.exe run.py --auth-tradingview`.
- Either way, **sign in on tradingview.com in the default browser first**. TradingView's
  CDN blocks its sign-in page ("ERROR: The request could not be satisfied") when that
  page is reached as a redirect from the approval URL.
