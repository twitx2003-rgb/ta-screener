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
.venv\Scripts\python.exe run.py --universe            # screener in market-cap bands -> data/universe/<date>
.venv\Scripts\python.exe run.py --bars --limit 50     # pilot: bars for the 50 largest
.venv\Scripts\python.exe run.py --bars                # all symbols (~55 min first time; resumable)
.venv\Scripts\python.exe run.py --update              # --universe (saved one if 429) + --bars
.venv\Scripts\python.exe run.py --check-bars          # bars vs trading calendar -> logs/bars_audit.json
.venv\Scripts\python.exe run.py --scan                # indicators + patterns -> data/scans/<session>/ (~3 min)
.venv\Scripts\python.exe run.py --scan-symbol NASDAQ:NVDA   # one symbol's detections with checklists
```

For a long run from a Claude Code session, start a detached process: the tool kills
background commands after about 10 minutes. `--bars` resumes where it stopped.

```
Start-Process .venv\Scripts\python.exe -ArgumentList "run.py","--bars" -WorkingDirectory C:\dev\ta-screener -RedirectStandardOutput logs\bars_full_stdout.txt -RedirectStandardError logs\bars_full_stderr.txt -WindowStyle Hidden
```

Exit codes: 0 ok, 1 failed, 2 bad args.

## Architecture rules — keep these

- `tascreen/tv/mcp_client.py` is the transport:
  - OAuth 2.1 with tokens in `~/.ta-screener/tv_tokens.json`, registered separately
    from market-research-pipeline so one project's token refresh never signs the other out.
  - The callback is `localhost:8766`.
  - `session()` / `with_session()` make many calls over one connection.
- `tascreen/universe.py` accepts a universe only if it is provably complete:
  - bands split on the row cap or the size limit;
  - every row lies inside its band;
  - the distinct symbols equal the unsplit `totalCount` (with one retry for caps
    moving across edges).
- `tascreen/bars.py` is incremental. The overlap must match what is stored (within
  0.05% on OHLC); a mismatch, such as a split, triggers a full refetch. Up-to-date
  symbols make no call. One symbol failing does not stop the run.
- Data layout lives in `tascreen/store.py`, with atomic writes. Timestamps are
  normalized by `contracts.canonical_timestamps`: Parquet round-trips turn them into
  ms/ZoneInfo, which pandas concatenates as `object`.
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
  (http, no secrets). **Sign in on tradingview.com in the default browser first**:
  TradingView's CDN blocks its sign-in page when that page is reached as a redirect.
- **What actually worked (2026-09-23, CLI 2.1.280):**
  - The server from `.mcp.json` did not show up in `/mcp`, even though the folder was
    trusted and the shared `settings.json` has `enabledMcpjsonServers`. A shared file
    evidently cannot approve its own repo's servers.
  - Fix: add it at local scope. Run
    `claude mcp add --transport http --scope local tradingview https://mcp.tradingview.com/mcp`
    in this folder, restart the session, then run `/mcp`, choose tradingview and
    authenticate.
  - Status is now **Connected**. Check it with `claude mcp get tradingview`.
- When typing `/mcp`, add a space before Enter, or autocomplete may pick a skill whose
  name contains "mcp".
- Skills live in `.claude/skills/` and agents in `.claude/agents/`.
  - Built:
    - skills: `tradingview-rules` (not named `*-mcp`: the /mcp autocomplete picked a
      skill with "mcp" in its name over the built-in command) and `bulkowski-patterns`;
    - agent: `pattern-verifier`.
  - Planned: the skills `daily-update` and `add-pattern`, and the agents
    `setup-analyst` and `screener-scout`.

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
- **`run-screener`** returned 429 on the first run (4 attempts over about 65 s). It
  answered about an hour later (`--discover screener_top5 screener_band`).
  - Shape: `{success, data: {rows: [...], totalCount}}`.
  - Default rows have 40 keys: `symbol` ("EXCHANGE:TICKER"), `name` (the ticker),
    `description`, `type`, `subtype` (e.g. "common"), `currency`, `sector`,
    `industry`, `market_cap_basic`, `close`, `volume`, `RSI`, `EMA50`, `EMA200`,
    `Recommend.All`, `recommendation`, `BB.lower`/`BB.upper`, `High.3M`/`Low.3M`,
    `Perf.*`, `earnings_release_next_date`, `days_to_earnings`, and similar.
  - There is **no `exchange` key in the default rows**; the exchange is the
    `symbol` prefix.
  - Any value may be null (e.g. `Perf.W`), so read values with
    `pick(..., allow_null=True)` where a gap is legitimate.
  - `subtype` exists in rows even though the column catalogue has no type column. The
    set of its values (preferred, ADR, ...) is still to be listed from the full universe.
- **`totalCount` for `market_cap_basic >= 1e9` with `symbol_types: ["stock"]` is about
  4,000, and includes OTC** (e.g. `OTC:...` symbols). Filters only take numeric ranges
  plus index/sector/industry, so OTC is dropped on our side by the symbol prefix, after
  fetching.
  - The 1-2B band alone is about 700 rows.
  - With the 1000-row cap, the bands must be narrow at the low end.
- The `get-ohlcv` notice says:
  - bars are delayed 15 minutes or more, and the last bar may still change;
  - there are **no pre- or post-market bars**;
  - prices are **split-adjusted only** (no dividend adjustment);
  - some exchanges come from an alternative or end-of-day feed.
- The `get-ohlcv` `summary` block holds avg_volume, period high/low, net change and
  similar values; we compute our own from the bars.

## Status

- **Phase 0 (skeleton + connection): DONE and approved 2026-09-23.**
  - Built: the TradingView client and its tests (copied and adapted, with `session()`
    added), config, contracts, the market calendar, `run.py`, `.mcp.json`,
    `.claude/settings.json` and the `tradingview-rules` skill.
  - `--auth-tradingview` and `--discover` ran live; the `run-screener` shape is mapped
    from a live answer (see above).
  - Claude Code's own MCP connection is **Connected**: a test `get-ohlcv` call ran
    without a permission prompt, so the allow rules match the real tool names.
  - Pushed to https://github.com/twitx2003-rgb/ta-screener (public, branch `main`).
  - Display note: the Windows terminal shows Hebrew reversed (no RTL support). The
    Claude Code panel inside VS Code renders it correctly.
  - Git identity for this repo only: `Claude <noreply@anthropic.com>`, the same
    identity as the sister project's commits, so no personal e-mail goes into a
    public history.
- **Phase 1 (universe + bars): BUILT 2026-09-23. Bars are partial: 1,008 of ~2,370**,
  the largest by market cap. Review is pending.
  - Code: `tascreen/universe.py`, `tascreen/bars.py` and `tascreen/store.py`.
  - **TradingView throttles `get-ohlcv` after heavy use.**
    - ~1,000 calls ran at 1.4 s each (about 25 minutes).
    - Then every call took 20-50 s. That included a separate session and 10-bar calls,
      so it is account- or server-side and not payload size, and it was still slow
      20 minutes later.
    - The run was stopped (it resumes: `--bars` skips up-to-date symbols).
    - Next: resume later with pacing (`bars.min_interval_s`), and measure the quota
      before deciding the daily schedule. A daily update is one call per symbol
      (~2,370 calls), so it may need to be spread out.
  - **Data quality (`--check-bars`):**
    - A few series are sparse, covering 20-93% of their sessions: thinly traded
      B-class shares and stocks uplisted from OTC or relisted. The scan skips patterns
      when recent coverage is < 0.95.
    - 66 NYSE symbols (none on NASDAQ) lack 2024-11-07, an ordinary session. It is a
      TradingView data hole: one bar, not a closure.
    - The audit counts a day as market-wide only when at least 20 symbols (or 5%)
      span it. A single sparse series reaching back to 2014 had turned each of its
      holes into a "market-wide" day.
  - What the live runs taught:
    - **The MCP server refuses results over 1,000,000 bytes.** The error is "Result
      size N exceeds limit of 1000000 bytes", it arrives as an MCP-level `is_error`,
      and it hits at about 430 default screener rows (1000 rows came to ~1.07 MB).
      So `universe.row_cap` is 400, and a too-big answer splits its band like one
      over the row cap.
    - **PyYAML reads `1.0e9` (no sign) as a string.** Sent as the filter floor, the
      screener ignored it and counted ~12,000 stocks instead of ~4,000. The
      completeness check caught it. Every numeric setting is now converted
      explicitly in its dataclass.
    - **The token expired mid-run and the refresh 404'd.** The token was valid when
      the session opened, so nothing was discovered, and the SDK guessed
      `mcp.tradingview.com/token`. Fix: `_StoredExpiryAuth._refresh_token` discovers
      the endpoint before every refresh, and the expiry is taken 60 s early (a 401
      mid-run would send the SDK to a browser sign-in).
      `tests/test_mcp_client.py::test_token_expiring_in_the_middle_of_a_session...`
      fails without the fix.
    - **The universe for 2026-09-23:**
      - The screener counts ~4,000 "stock" rows above $1B.
      - About 1,300 OTC and 300 `subtype: preferred` are dropped after the completeness
        check (`universe.drop_subtypes`; a preferred issue carries the parent's
        market cap). That leaves **~2,370 common stocks**, mostly NYSE and NASDAQ, a few AMEX,
        one CBOE.
      - The fetch took 14 bands.
      - Subtypes seen: only `common` and `preferred`. ADRs are not separate.
    - **Bars speed:** 1.39 s per `get-ohlcv` call, sequential, one session per 100
      symbols. That is ~55 min for a first fill of ~2,370 symbols, and the same for a
      daily update (one call per symbol). MCP responses carry
      `x-ratelimit-limit: 100` (the window is unknown), so concurrency stays 1 until
      it is measured.
    - **Bar details:**
      - Timestamps are 13:30 or 14:30 UTC (the session open; DST shifts it).
      - Most symbols have the full 600 bars; recent IPOs have fewer.
    - **Unscheduled closure:** every symbol lacked 2025-01-09 (national day of
      mourning, President Carter). It was added to
      `market_hours.UNSCHEDULED_CLOSURES`. `--check-bars` reports any future
      market-wide missing day.
- **Phase 2 (scan): BUILT 2026-09-23, run in parallel with phase 1 (user's choice).
  Review is pending.**
  - Rules:
    - `tascreen/patterns/rules.yaml` holds every threshold, each with `origin` (site or
      ours) and a note. It was written from thepatternsite.com, read the same day. The
      free site has no numeric candle definitions (tall, small, doji, trend), so those
      numbers are all ours.
    - Bulkowski's measure rule multiplies by his statistics; we use the classic full
      height and never ship his statistics.
  - Code: `indicators.py`, `patterns/{pivots,detection,candles,chart,rules}.py`, `scan.py`.
    `--scan` writes `data/scans/<session>/`. `--scan-symbol X` prints detections with
    their checklists.
  - First live scan (1,008 symbols, 160 s):
    - Our RSI14, EMA50 and EMA200 agree with TradingView's own values for 99.9%,
      99.3% and 98.8% of symbols (median difference about 0).
  - Tightened after looking at live output, with each change in rules.yaml:
    - `pole_min_atr_per_session` 1.0: the median flag pole moved 0.5 ATR/session, and
      483 "flags" became 27.
    - `touch_tolerance_height` 0.1 of the pattern height.
    - `converge_max_end_width` 0.75: near-parallel rising channels had passed as
      wedges; wedges dropped by about a third.
    - Each fix has a synthetic test, including "a rising channel is not a wedge" and
      "a V is not a cup".
  - Claude Code: the `bulkowski-patterns` skill, and the `pattern-verifier` agent
    (read-only, re-derives rules from raw bars).
    `tests/test_claude_config.py` checks that agents list explicit tools, that none
    edits files, and that none gets a TradingView write tool.
  - Open items:
    - a QA sample per pattern with `pattern-verifier` (run from a Claude Code session
      in this folder, where the MCP is connected);
    - the user reviews rules.yaml;
    - a full scan once all bars are in.

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
