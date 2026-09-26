# Subsystem notes (moved out of CLAUDE.md, 2026-09-26)

How each part works and why, as recorded while it was built. CLAUDE.md keeps the rules;
read the section you need here.

## Website, live data, channels, hosting, agents

- **The website (`tascreen/web/`) only reads** data/ and logs/; it never calls
  TradingView, so it can stay open during `--bars`.
  - It binds to `web.app.HOST` = 127.0.0.1; the bind address is deliberately not a
    setting. `TrustedHostMiddleware` refuses Host names other than 127.0.0.1,
    localhost and `web.public_hosts` (DNS rebinding).
  - **Public through a tunnel (owner's decision, 2026-09-23).** The owner chose twice
    to make the site public with its data, after being told that TradingView's terms
    limit the data to the account holder and the account could be blocked.
    - The owner opens the tunnel themselves: VS Code port forwarding, port 8050,
      visibility Public. `web.public_hosts: ["*.devtunnels.ms"]` lets the site
      answer there.
    - Claude Code's auto-mode permission check blocked Claude from opening a tunnel
      and from some edits toward it ("data exfiltration"). Do not work around it:
      the owner does those steps.
    - A non-empty `public_hosts` turns on public mode: scan error texts (file paths,
      the Windows user name) are not shown.
    - Other headers: nosniff, Referrer-Policy same-origin, and frame-ancestors 'none'.
    - The "back" link only uses a same-host referer.
    - Tests cover all of it.
    - To close the site: Stop Forwarding Port in VS Code, or `public_hosts: []`.
  - `ScanRepository` reloads when a newer `scan.json` appears.
  - `/screener` and `/api/scan` share one filter model (`web/filters.py`), and filters live in
    the URL. A bad value is dropped and reported in Hebrew, never guessed.
  - No raw nan/None/NaT on a page: formatters print "—", `fmt.script_json` refuses
    NaN, and `fmt.page_problems` is asserted on every page in `tests/test_web.py`.
  - RTL: numbers go in `num` spans (LTR). English names get LTR isolation (`.co`),
    or a trailing period jumps to the wrong end. Numeric inputs are LTR, so their
    placeholders are digits only; Hebrew in them comes out reversed.
  - Hebrew wording for sectors, statuses and rule parameters lives in `web/labels.py`;
    a test checks that every rules.yaml parameter has a label.
  - Chart: TradingView Lightweight Charts 5.2.1, vendored in `web/static/vendor/`
    with its LICENSE/NOTICE. The API was read from its typings, v5 style:
    `addSeries(LC.CandlestickSeries, ...)`, `createSeriesMarkers`. The footer
    NOTICE line and `attributionLogo` are licence requirements.
- **Live quotes (`tascreen/quotes.py`, user request 2026-09-23: "updates every few
  minutes").**
  - `--live` refreshes every stock's last price every `live.interval_minutes` (5)
    during the US session, plus `after_close_minutes` (20), since quotes are delayed.
    One pass is the universe's band fetch with a quote row mapper (~15 screener calls).
  - After the close it runs `--update` once per session (`data/quotes/live_state.json`).
    The bars part stops at `next_open - 10 min` (`BarsJob.stop_at`); unfetched symbols
    are "deferred" and come first the next night.
  - Row fields: `close` is the last price. `change` is percent, verified live against
    `change_abs`. `relative_volume_10d_calc` is also read.
  - **A quote is not a close.** Patterns stay close-based (Bulkowski).
    - The scan stores, for every chart pattern still forming, `trigger_up` /
      `trigger_down`: the level a close must cross in the next session.
    - Each detector computes it from its own confirmation rule: the middle peak or
      trough, the neckline at `last+1`, the trendlines at `last+1`, the flag lines,
      the HTF high, the cup's right lip.
    - The site shows a "crossing now, not final until the close" chip when the live
      price is past it.
    - Only for stocks whose last bar is the session right before the quotes'
      session, since the trigger was computed for exactly that session.
  - Web: `QuotesRepository` reloads on `latest.json` mtime.
    - `live_for()` applies quotes only when their session is later than the scan's
      and they are fresher than `interval x stale_after_intervals`; stale quotes get a
      warning banner and are not used.
    - `with_live_prices()` swaps `close`/`change_1d_pct` so filters and sorts use them.
    - `live=cross` is a filter.
    - Pages poll `/api/live` every 60 s and reload on new quotes, keeping the scroll
      position and never reloading while a filter field is focused.
  - First live try (2026-09-23, about 12:50 New York, during the session): the
    screener answered 429 on every retry. The live loop logs it and tries again next
    round.
  - Git Bash's `TZ=America/New_York date` printed a wrong New York time on this machine
    (it said 16:48 when it was 12:48). Get market times from Python's `zoneinfo`.
    The live UI was checked on synthetic data (screenshots): banner, dots, crossing
    chip, trigger line.
- Tests are offline:
  - an in-process MCP server;
  - a local OAuth server for the sign-in flow (`tests/test_mcp_client.py`).
- Bulk work (hundreds of symbols) goes through `run.py`, never through MCP tool calls
  in a conversation.

- **Discussion channels (`tascreen/channels/`, user request 2026-09-23).** The home
  page is a Discord-like channel view.
  - Channels: #כללי, plus the `channels.count` (8) most common patterns in the newest
    scan. They appear in a right-hand sidebar that also holds the tools: screener
    (`/screener`), glossary and status.
  - Old `/?family=...` links 307 to `/screener?...`.
  - **Authors are simulated members, AI agents, labelled on every post and in every
    channel header.** They are 6 fictional personas (`channels/personas.yaml`). The
    channels are read-only (owner's decision).
  - Written through the owner's Claude Code subscription (owner's decision), in
    `tascreen/llm.py` (`ClaudeCodeLLM`, adapted from market-research-pipeline). Checked
    on CLI 2.1.280:
    - npm now ships a native `bin/claude.exe`, which is run directly (no cmd.exe
      quoting); flags: `-p --output-format json --json-schema --tools "" --strict-mcp-config
      --no-session-persistence --model --effort --system-prompt`.
    - `--bare` is avoided: it accepts only an API key, i.e. paid API billing.
      ANTHROPIC_API_KEY is removed from the child's environment.
    - **It refuses inside Claude Code (`CLAUDECODE=1`)**, so `--channels` and the channel
      part of `--live` must run from a normal terminal. Inside Claude Code, `--live` logs
      "channels are off" and keeps refreshing quotes.
  - One call per channel per day, with JSON output by schema. Each topic is a stock
    picked by `select.pick_topics` (fresh breakouts first). The call returns a thread:
    a chart post, then 2-3 replies. #כללי also gets a counts recap. During the session,
    each new crossing gets a thread in its pattern's channel, deduplicated per
    (symbol, pattern, session) and capped at `live_max_per_hour`.
  - **Grounding** (`generate.post_problem`); a failing post is dropped with its
    reason, and a thread without its chart post is dropped:
    - every post cites fact keys from `channels/brief.py`;
    - every number in text or caption must match a fact value of the thread's brief,
      within 0.5%; dates, counts < 20 and indicator periods pass;
    - no advice, trading-claim or forecast words; Hebrew prefix letters are peeled off
      before matching.
  - "כלל המדידה" and "לא סופי עד הסגירה" are appended when a post leaves them out.
  - Charts are `channels/chart_svg.py`: a server-side SVG "screenshot" (fixed dark
    look) with a clean annotation layer (lines, markers, arrows, pill tags; all
    `class="ann"`).
    - The agent chooses from `DRAWINGS`; the geometry comes from the detection.
    - Tags never cover each other (`_Annotations._free_y` moves a tag up or down).
    - The caption goes in the emptiest corner.
    - Stored SVGs keep the look they were drawn with: after changing `chart_svg`, run
      `--redraw-charts` (no model call). It draws from the thread's own `record` (the
      detection it discussed, stored with `key` since 2026-09-24) on bars cut at the
      thread's day; older threads fall back to the newest scan's detection.
  - Storage: `data/channels/<day>/<channel>.json`, `live.json`, `charts/*.svg`. Pages
    poll `/api/stamp` and reload on new posts or quotes.
  - Post numbers are wrapped in `<bdi>` (`fmt.post_text`), so "ב-85.0" is not
    reordered by RTL.
  - **First real run (owner's terminal, 2026-09-23, model alias `sonnet`):**
    - 8 of 9 channels written: 24 threads, 97 posts, 0 dropped by the checks,
      33-55 s per channel.
    - API-equivalent about $0.06-0.13 per channel; not billed on the subscription.
    - The 9th failed on the subscription's **session limit**. The CLI returns
      `{"subtype": "success", "is_error": true, "result": "You've hit your session
      limit · resets ..."}`.
    - Now `llm.UsageLimit`: the daily run stops trying the remaining channels (a rerun
      of `--channels` writes only the missing ones), and live posts pause for an hour.
    - Sample checked against the scan: the NWSA head-and-shoulders thread's breakout,
      target, close, relative volume, volume trend and neckline slope all matched,
      and the #כללי recap counts matched exactly.
    - Qualitative claims (e.g. "the neckline is rising") are not machine-checked;
      this one was right.
    - A unit letter after a number ("1.37x") was reordered by RTL; `post_text` now
      isolates it with the number.
- **Design (owner's answers, 2026-09-24):** a social-community look.
  - Purple accent (`--accent #A78BFA`); always dark unless the visitor picks light (only
    "light" is stored); Heebo for text, IBM Plex Mono for numbers.
  - Channel posts are Telegram-style bubbles: the avatar sits at the start side (right,
    RTL), and replies show a quote of the post they answer.
  - Each persona has a line-icon avatar (`personas.yaml` `icon`; drawn by the
    `_icons.html` macro) and its own color.
  - The chart fills the bubble (medium); a click opens it large in a `<dialog>`.
  - Screener: below 1700 px the sidebar leaves no room for all columns, so the columns
    52-week high, ATR% and sector (`.opt`) are hidden there.
- **Hosting (owner's decision, 2026-09-24): GitHub Actions + Vercel, free, no credit card.**
  (Oracle's Always Free VM was the first choice; the owner did not want a card. The
  `deploy/` scripts for a VM stay as the fallback, e.g. if GitHub restricts the usage.)
  Plan stages: 0 probe, 1 daily update on Actions, 2 static site on Vercel, 3 cut-over
  (home computer stops), 4 live data, 5 client-side screener, 6 upkeep.
  - **Stage 0 probe (2026-09-24, `.github/workflows/probe.yml`, `run.py --ci-probe`):**
    from a GitHub runner, get-ohlcv works (300 calls, median 0.8 s, a few timeouts);
    Claude Code with `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) works;
    Vercel works; the TradingView screener answered 429 (as at home, often for hours);
    **TradingView rotates the refresh token on every refresh.**
  - **State:** the PRIVATE repo `twitx2003-rgb/ta-screener-state` holds the runners'
    own TradingView sign-in (`tv_tokens.json`, a separate OAuth client made on the PC
    with `TA_TV_TOKEN_PATH=~/.ta-screener/tv_tokens_ci.json run.py --auth-tradingview`),
    `data/` (minus bars) and full logs. Bars live in its `bars` release asset.
    `.github/state.sh restore|save|save-token`; `save` is one commit with no history,
    force-pushed. A refreshed token is pushed at once (`mcp_client.push_token_file`,
    `TA_STATE_DIR`), or a dead run would lose the only valid one.
  - **Secrets in the public repo:** STATE_REPO_TOKEN (fine-grained key: only the state
    repo, Contents read/write), CLAUDE_CODE_OAUTH_TOKEN, VERCEL_TOKEN,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
  - **Public logs:** the Actions log shows only `logs/ci_summary.json` (counts, dates,
    error class names); everything else goes to the state repo. Python never prints
    token-derived text (a mask line once carried a token into a log file; the state
    history was rewritten); `.github/mask_tokens.py` masks from the token file.
  - **Stage 1 (`.github/workflows/run.yml`, `run.py --ci-tick`):** does what is due: the
    daily update when the last completed session has no complete update yet (bars
    deferred or failed -> the next run finishes it), then the channels. `--max-minutes`
    bounds the bar fetch. Manual inputs: limit, channels, save. First full run: the
    first 600-bar fetch is slow from GitHub (4-9 s a call, ~3 h for all); a TradingView
    session that failed to open stopped it at 400, so a failed session now costs its
    batch only (`bars.MAX_BROKEN_SESSIONS`). The ledger now re-reads a day whose scan
    was run again (meta `ingested` = day -> scan created_at).
  - **Stage 2 (`tascreen/web/export.py`, `run.py --export-site DIR`):** the same app in
    static mode through TestClient -> folder pages (/c/<id>/, /symbol/<EXCHANGE_TICKER>/,
    /screener/pattern/<key>/, /screener/preset/<slug>/), compact charts (200 sessions,
    column arrays), no live data yet, no filter form (stage 5), content-hash stamp in
    /data/stamp.json, vercel.json (trailing slashes, headers). Real data: ~2,440 files,
    ~65 MB (Vercel's CLI limit is 100 MB; the export refuses > 85 MB). Deterministic.
    run.yml publishes after a saving run (`vercel link --project ta-screener`, deploy).
  - **Telegram (`tascreen/notify.py`):** the owner's bot @ta_screener_alert_bot;
    `run.py --setup-telegram` once (hidden token, finds the chat, test message, saves
    ~/.ta-screener/telegram.json); run.yml sends a Hebrew summary after each run
    (`--ci-notify`). Messages carry counts and links only.
  - **Stage 3 cut-over (2026-09-24):** the owner stopped `--live` on the PC; the PC's
    ledger (with the backfill) and channels were copied to the state repo
    (`deploy/seed_state.py`); run.yml got its schedule (21:40 UTC Mon-Fri, catch-ups
    01:10 and 05:10 UTC Tue-Sat; a scheduled run writes channels and saves). The PC
    no longer runs anything. Site: https://ta-screener.vercel.app (no live prices
    until stage 4; the screener shows the first 500 rows until stage 5).
  - `config.local.yaml` (gitignored) overrides single keys on one machine; the runner
    writes one (paths into the state checkout, `live.update_after_close: false`).
- **Professional agents plan (owner-approved 2026-09-24; stop for review after each
  stage):** A1 outcome ledger, A2 backfill, A3 follow-up posts, B qualitative facts +
  earnings + market overview, C editor (Haiku; posts wait when it cannot run) + memory,
  D guardian (Telegram alerts) + weekly audit. All run automatically; no Claude Code
  subagents (owner's choice). No Bulkowski statistics anywhere (book rule above).
- **A1 — outcome ledger (built 2026-09-24).**
  - `patterns/levels.py` is the one source for `detection_key` and `invalidation`,
    used by the ledger, the chart's "failure" drawing and (stage B) the agents' facts.
  - Key = symbol|pattern|first point|last point. Start alone collided: triangles and
    wedges report two patterns from one first pivot (the first real rebuild merged 42).
  - Invalidation = back beyond the whole pattern (lowest/highest turning point; for
    flags, pennants and high-tight flags the consolidation's low/high after the pole's
    top; for the cup the handle's low). The first version used the opposite trendline
    at the breakout; near a triangle's apex that is almost the breakout level, and
    ~90% of triangles came out "failed".
  - `outcomes.py`: ledger `data/outcomes/ledger.parquet` (contract OUTCOMES) + meta.json.
    Target = high/low touch; failed = close beyond invalidation; both on one bar =
    failed; expired after `outcomes.max_sessions` (60). First-seen values are kept;
    later differences only bump `restated`. `scale` follows splits. A changed
    `max_sessions` re-evaluates every row.
  - Runs after every scan (`run.py scan()`), and `--outcomes [--rebuild]`.
  - `/scorecard` ("לוח תוצאות") + `/api/scorecard`: counts per pattern, percentages
    only from `outcomes.min_cases` (20) decided breakouts, our definitions stated,
    not the book's statistics.
  - First real build (2 scans, 2026-09-22/23): 1,187 breakouts; three rows re-checked
    by hand against the bars matched. Measure-rule targets sit far (median ~20-30%)
    while invalidation is near (~3-10%), so failures resolve first; early rates lean
    to "failed" until targets have time.
- **A2 — backfill (built 2026-09-24).** `tascreen/backfill.py`: `detect_chart` on the
  stored bars cut at every session (`outcomes.backfill_step: 1`) from
  `backfill_min_bars` (150) up to the day before the first saved scan, behind the
  scan's own coverage gate (`scan.patterns_allowed`); a breakout is kept the first
  time a cut reports it, exactly as the daily ingest would (source "backfill").
  - `tests/test_backfill.py` proves the no-look-ahead claim: run_scan + update day by
    day on synthetic bars gives the same rows as `backfill_symbol` (incl. a flag).
  - Per-symbol checkpoints in `data/outcomes/backfill/` + manifest (rules digest, step,
    start); a change starts over. `ProcessPool` of `backfill_workers` (4 of 8 cores).
  - Merge only adds unknown keys (`outcomes.add_new`): the daily scans' rows win.
    `--outcomes --rebuild` re-adds the saved backfill. Ledger writes take
    `store.ledger_lock()` (the live loop and a backfill may finish together).
  - Pilot on the 50 largest: ~9 s per symbol per process -> ~1.5 h for all with 4.
  - Survivorship: today's universe only; the scorecard says so.
- **The long average is SMA150, not SMA200 (owner's decision, 2026-09-23).**
  - Golden and death crosses are 50/150.
  - EMA200 stays only for the TradingView cross-check.
  - INDICATORS has `sma150`/`above_sma150`, and the filter parameter is `sma150`.
  - Rescanned the same day.

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
    `setup-analyst` and `screener-scout`. The agents should read the site's JSON
    (`/api/scan` with the page's filters, and `/api/symbol/{EXCHANGE:TICKER}` with
    checklists) rather than parquet files.

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

