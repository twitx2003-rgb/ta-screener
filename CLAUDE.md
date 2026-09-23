# CLAUDE.md — project brief for Claude Code

A local, Hebrew-language website that screens US stocks above a $1B market cap by
technical analysis. It filters on:

- chart patterns, following Bulkowski's *Encyclopedia of Chart Patterns*;
- candlestick patterns, following Bulkowski's *Encyclopedia of Candlestick Charts*;
- indicators.

Data comes from TradingView's official MCP server. **Personal, non-commercial use
only**; see `LICENSES.md`. Built phase by phase, and the owner reviews each phase
before the next one starts.

**Talk to the user in Hebrew.** Code, comments and commit messages stay in English.
The user wants you to run commands yourself: "whatever you can run yourself, run it".
Prefer code that asks for or does things itself over telling the user to edit files.

## Environment (the user's machine)

- Windows. The project is at `C:\dev\ta-screener`.
- **Python 3.14.4**, with a venv at `.venv` (`py -3.14 -m venv .venv`).
  - Do **not** use Python 3.12: the Microsoft Store 3.12 on this machine is broken.
  - Do not change system Python installs.
- Call the venv interpreter directly: `.venv\Scripts\python.exe ...`.
- VS Code's terminal is PowerShell.
- Sister project: `C:\dev\market-research-pipeline`. The TradingView client, the
  contracts and the market calendar were copied from it; the lessons behind them are
  recorded in the module docstrings.
- **The GitHub repo is public** (user decision, 2026-09-23). Therefore:
  - Never commit vendor data (TradingView prices, volumes, market caps, payloads), not
    even a few numbers in notes, commit messages or test fixtures. Use synthetic values.
  - Never commit text or statistics tables from Bulkowski's books. Rules are written in
    our own words, with a chapter or URL reference.
  - `data/`, `logs/` and `.env` are gitignored.

## Commands

```
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -q                 # offline tests; run before every push
.venv\Scripts\python.exe run.py --auth-tradingview    # one-time browser sign-in (Python client)
.venv\Scripts\python.exe run.py --tradingview-token-status
.venv\Scripts\python.exe run.py --tradingview-tools   # list tools -> logs/tradingview_tools.json
.venv\Scripts\python.exe run.py --tradingview-call TOOL key=value ...
.venv\Scripts\python.exe run.py --discover            # probe screener/columns/ohlcv -> logs/discover/
.venv\Scripts\python.exe run.py --discover screener_top5 screener_band   # retry just those
.venv\Scripts\python.exe run.py --tradingview-diagnose
```

Exit codes: 0 ok, 1 failed, 2 bad args.

## Architecture rules — keep these

- `tascreen/tv/mcp_client.py` is the transport:
  - OAuth 2.1 with tokens in `~/.ta-screener/tv_tokens.json`, registered separately
    from market-research-pipeline so one project's token refresh never signs the other out.
  - The callback is `localhost:8766`.
  - `session()` / `with_session()` make many calls over one connection.
- `tascreen/tv/data.py` checks payloads:
  - `tool_payload` turns `{"success": false}` into ToolFailed or RateLimited.
  - `bars_frame` checks bars.
  - An unknown shape is saved and raised as `ShapeNotMapped`.
- **Read only.** `is_read_only()` refuses alert and watchlist writes. The same 10
  tools are denied in `.claude/settings.json`, and `tests/test_claude_config.py` keeps
  the two in sync.
- **Never guess a field.** Payload fields go through `tascreen.fields.pick()`. Screener
  columns come from the live catalogue (`--discover`), never from memory.
- Every stored frame is checked against a contract in `tascreen/contracts.py`.
- A daily bar is a close only after `market.session_close` (16:15 New York); see
  `tascreen/market_hours.py`.
- Config is in `config.yaml`. Unknown sections and keys are rejected.
- Tests are offline:
  - an in-process MCP server;
  - a local OAuth server for the sign-in flow (`tests/test_mcp_client.py`).
- Bulk work (hundreds of symbols) goes through `run.py`, never through MCP tool calls
  in a conversation.

## Claude Code integration

- `.mcp.json` points the `tradingview` server at `https://mcp.tradingview.com/mcp`
  (http, no secrets). Authenticate once with `/mcp`. **Sign in on tradingview.com in
  the default browser first**: TradingView's CDN blocks its sign-in page when that page
  is reached as a redirect.
- Skills live in `.claude/skills/` and agents in `.claude/agents/`.
  - Built: `tradingview-rules` (not named `*-mcp`: the /mcp autocomplete picked a skill with "mcp" in its name instead of the built-in command).
  - Planned: `bulkowski-patterns`, `daily-update`, `add-pattern` (skills);
    `pattern-verifier`, `setup-analyst`, `screener-scout` (agents).

## Verified facts (from market-research-pipeline's live runs, 2026-09)

- TradingView's server lists 35 tools (`mcp-tv-*`, `mcp-watchlist-*`).
- A failed call has `is_error: false` and a payload of `{"success": false, "error": ...}`.
- Scanner-backed tools answer 429 for hours at a time: `run-screener`,
  `get-symbol-data(-batch)`, `get-earnings-calendar` and `get-financials`. `get-ohlcv`
  has not been rate limited.
- `get-ohlcv` returns bars `[{t,o,h,l,c,v}]`, oldest first, with `t` in unix seconds at
  the session open. The newest bar can be today's live session.
- `run-screener` arguments:
  - `filters` as `{column: [min, max]}`, plus the keys `index`, `sector`, `industry` and
    `analyst_rating`;
  - `limit` capped at 1000, with **no offset**;
  - `symbol_types`, `columns`, `sort_by`, `filter_preset`.
- `market_cap_basic` is the market-cap column, in raw USD.
- mcp 2.2 SDK gaps, both worked around in `_stored_expiry_auth`:
  - the SDK forgets a stored token's expiry;
  - a refresh made before discovery guesses the token endpoint.
- The access token lasts 900 s, and TradingView issues refresh tokens.
## Live `--discover` results (2026-09-23, this project's first run)

- Sign-in with a separate OAuth client and tokens in `~/.ta-screener` worked on the
  first attempt.
- **`get-screener-columns` is not rate limited.**
  - With no group, it returns `{success, count, hint, groups: [{group, count, columns: [names]}]}`
    across 17 groups.
  - With a group or search, it returns `{success, count, columns: [{name, description, group, markets}]}`.
  - A search with no match is `success: false`, with the error "no columns matched".
- **The catalogue has no candlestick, chart-pattern or moving-average columns.** Candles,
  patterns and SMA/EMA are therefore computed from our own bars. Do not assume TradingView
  scanner names (such as `Candle.*` or `SMA50`) exist: they are not in the catalogue.
- Technicals group:
  - Present: `RSI`, `ATRP`, `ADX`, `AO`, `Aroon`, `BB`, `BBPower`, `CCI`, `MACD.macd`,
    `Mom`, `Stoch`, `ADRP`, `TechRating_1D`, `MARating_1D`, `OsRating_1D` and `AnalystRating`.
  - 1D is the default; other timeframes take a `|` suffix (`|1W`, `|60`, and so on).
  - `RSI` and `ATRP` are the cross-check candidates for our own indicators.
- Other columns:
  - Exchange: `exchange` (group price).
  - Issuer country: `country` (group identity).
  - There is **no type, subtype or ADR column**. `symbol_types` is the only stock filter.
  - Liquidity: `average_volume_10d_calc`, `relative_volume_10d_calc`, `Value.Traded`
    and `AvgValue.Traded_10d`.
  - Market cap: `market_cap_basic`.
- **`run-screener` is still unmapped.** It returned 429 on every attempt of this run: 4
  attempts over about 65 s, on `scanner.tradingview.com/america/scan`. Retry later; its
  response shape stays unknown until then.
- The `get-ohlcv` notice says:
  - bars are delayed 15 minutes or more, and the last bar may still change;
  - there are **no pre- or post-market bars**;
  - prices are **split-adjusted only** (no dividend adjustment);
  - some exchanges come from an alternative or end-of-day feed.
- The `get-ohlcv` `summary` block holds avg_volume, period high/low, net change and
  similar values; we compute our own from the bars.

## Status

- **Phase 0 (skeleton + connection): BUILT 2026-09-23.**
  - Built: the TradingView client and its tests (copied and adapted, with `session()`
    added), config, contracts, the market calendar, `run.py`, `.mcp.json`,
    `.claude/settings.json` and the `tradingview-rules` skill.
  - `--auth-tradingview` and `--discover` ran live; see above.
  - Open items:
    - map the `run-screener` shape once the scanner stops answering 429;
    - the user authenticates Claude Code's own MCP connection (`/mcp` in a session
      opened in this folder);
    - the user creates the public GitHub repo (`gh` is not installed).
  - Git identity for this repo only: `Claude <noreply@anthropic.com>`, the same
    identity as the sister project's commits, so no personal e-mail goes into a
    public history.

## Plan (user-approved 2026-09-23; stop for review after each phase)

**Decisions:** Bulkowski is the book. v1 filters on chart patterns, candlesticks and
indicators. The site is local only (127.0.0.1). This is a separate project in a new
**public** repo. Updates are manual at first; decide on scheduling after measuring.

**Phase 0 — skeleton + connection.**
- Sign in: `--auth-tradingview`, and `/mcp` for Claude Code.
- Run `--discover` and map the screener and columns shapes from the real payloads.
- Create the GitHub repo: `gh` is not installed, so the user creates it on github.com.

**Phase 1 — universe + bars.**
- Universe (`universe.py`):
  - `run-screener` with market america, `symbol_types` stock and
    `market_cap_basic >= 1e9`, excluding OTC.
  - Split into market-cap bands with bisection until every band returns its whole
    `totalCount`; the bands must add up to the total, or the run halts.
  - On a 429, reuse the last snapshot if it is at most 7 days old, and show its date.
- Bars (`bars.py`):
  - `get-ohlcv` 1D, `count=600` on first fetch.
  - Incremental updates overlap by 5 bars. If the overlap mismatches (for example a
    split), refetch the whole history.
  - Store as `data/bars/<EXCHANGE_TICKER>.parquet`.
  - Open a new session every ~100 symbols, because tokens last 900 s. Runs can resume.
  - Pilot with `--limit 50` to measure seconds per symbol.

**Phase 2 — scan.**
- `indicators.py`:
  - SMA 20/50/200, EMA21, RSI14 (Wilder), ATR14 and ATR%;
  - distance from the 52-week high and low;
  - relative volume and average dollar volume;
  - golden and death crosses.
  - Cross-check a sample against the screener's own columns, if it has them.
- `candles.py`: about 16 Bulkowski candlestick patterns, each with prior-trend context.
- `chart.py`: about 12 Bulkowski chart patterns built on ATR-scaled zigzag pivots.
  - Patterns: double top and bottom, triple top and bottom, head and shoulders (and
    inverse), ascending, descending and symmetrical triangles, rising and falling
    wedges, rectangles, flags and pennants, high-tight flag, cup with handle.
  - Each detection records: direction, status (forming, breakout, failed), pivots,
    breakout, height, volume trend, and a per-rule checklist (value, threshold, pass).
  - The measure-rule target is always labelled "the book's measure rule — not a
    forecast".
- `patterns/rules.yaml` is the single source of parameters. Each parameter has a
  `source` and a `confirmed_from_book` flag; unconfirmed numbers are shown to the user
  to check against their copy.
- Output: `data/scans/<date>/`.
- Also in this phase: the `bulkowski-patterns` skill, the `pattern-verifier` agent, and
  a QA sample.

**Phase 3 — website.**
- FastAPI + Jinja2 + uvicorn on 127.0.0.1:8050, Hebrew RTL, dark mode.
- "לא ייעוץ השקעות" on every page.
- TradingView's lightweight-charts (Apache-2.0) is vendored, with its attribution kept.
- Pages:
  - `/` — the screener, with filters kept in the URL;
  - `/symbol/{EXCHANGE:TICKER}` — the chart with pattern lines and a rule checklist;
  - `/patterns` — a Hebrew glossary;
  - `/status` — freshness, coverage and 429s;
  - `/api/scan`.
- Screenshots with headless Edge.

**Phase 4 — remaining skills and agents.** Skills `daily-update` and `add-pattern`;
agents `setup-analyst` and `screener-scout`. Decide on scheduling from the measured
run times.

## Safety

- Never print, log or commit tokens (`~/.ta-screener/tv_tokens.json`) or `.env`.
- Never publish `data/` or `logs/`, and never serve the site beyond 127.0.0.1.
- Nothing here is investment advice. Pattern targets are the book's measure rule, not
  forecasts.
