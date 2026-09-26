# Project history (moved out of CLAUDE.md, 2026-09-26)

Phase by phase, as it happened, with the owner's decisions. CLAUDE.md has the current
state; this file is the record.

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

- **Phases 1+2 approved by the user 2026-09-23** ("מאשר").
  - At that point the `get-ohlcv` throttle had not lifted. A single call (with its own
    session) took 56 s about 2 hours after the heavy run, and 89 s about 3 hours
    after it. So the throttle lasts hours, not minutes. Before resuming, time one
    call: about 3 s means it is clear.
  - So bars are still 1,008 of ~2,370; resuming with pacing is pending.
- **Phase 3 (website): BUILT 2026-09-23. Review is pending.**
  - Code: `tascreen/web/{app,data,filters,fmt,labels}.py`, templates and static files.
    `run.py --serve` serves 127.0.0.1:8050 (config section `web`: port,
    open_browser, rows_per_page).
  - Pages:
    - `/`: the screener. A filter panel covers stock, patterns and indicators, and
      filters live in the URL. There are quick presets and removable
      active-filter chips. The table is sortable, with the patterns column second.
    - `/symbol/X`: the chart with the selected detection's lines, points, breakout
      and target price lines, a picker, and a card per detection with its checklist
      and the origin of each threshold.
    - `/patterns`: the glossary built from rules.yaml, with Hebrew parameter
      labels, site/ours chips and live counts.
    - `/status`: scan, cross-check, universe, bars and audit.
    - `/api/scan` and `/api/symbol/X`.
  - Checked live on the 1,008-symbol scan: every page 200, no nan/None, 40-110 ms
    per page (the first load of a scan ~300 ms).
  - Screenshots with headless Edge, which on this machine needs all of these:
    - Windows paths for `--screenshot` and `--user-data-dir` (MSYS paths write nothing);
    - a separate `--user-data-dir` per shot (a reused profile silently skipped the
      second shot);
    - `Start-Process -Wait` from PowerShell;
    - `--virtual-time-budget=8000` for the chart.
    - Headless windows are at least **504 px** wide: a 400 px shot shows the left
      part of a 504 px page. Phone layouts are therefore checked at 500 px.
    - `--blink-settings=preferredColorScheme=1` forces the light theme.
  - Fixed from screenshots:
    - the patterns column was last and off-screen, so it now comes second;
    - English names had their final period on the wrong side;
    - Hebrew placeholders in numeric inputs came out reversed;
    - on phones, the grid column must be `minmax(0, 1fr)`, or the table widens the page;
    - candle detections sorted before chart patterns.
- **Chart analyst (owner's idea 2026-09-24; plan T1-T6, stop after each).** The owner
  sends a symbol and gets a Hebrew analysis, a chart and a Pine Script. Code: `tascreen/analyst/`.
  - T1 engine (`facts.py`, `zones.py`, `signals.py`, thresholds in `rules.yaml`): zones,
    trendlines, Fibonacci, RSI/MACD/divergences, averages, volume profile, the scanner's
    patterns -> facts `{value, label, unit}` + drawings with ids.
  - **Owner decision: a simple picture** ("clean, to the point"). `view.py` = the 2 nearest
    zones each side; Fibonacci 38.2/50/61.8 only while the price is inside a pullback;
    the volume profile only when its POC is within 3 ATR; a Fibonacci level or the POC on a
    zone joins that zone's label. The SVG and the Pine Script both draw this view.
  - T2 Pine (`pine.py`): v6, one switch per part in the settings; anchors by
    year/month/day; warns (middle of the chart) on another symbol, a non-daily chart, or
    closes >0.5% off (dividend-adjusted chart). Verified by the owner in TradingView: it
    compiles and draws. A script is for ONE symbol (the owner first added NVDA's to TMC).
  - T3 text (`writer.py`, `knowledge/*.md` with sources): one `claude -p` call (Sonnet,
    effort high), sections by name with cited fact keys; the channels' number/advice
    checks plus dates (DD/MM/YYYY, fact dates only) and bullish/bearish/trend words
    (a cited fact must say so); one retry with reasons; what still fails is left out and
    the message says so. `--analyze NVDA [--telegram]` (short names are resolved against
    the stored bars): chart PNG (`png.py`: rsvg-convert, else headless Edge; Edge writes
    nothing when started from Git Bash here, fine from PowerShell), HTML text, .pine.
    First live text (NVDA): every number and date right; it leaked an id "(tl_1)" (now
    rejected). **Owner's choice: short and simple** = headline, levels, up, down + at most
    2 optional one-sentence sections (only required ones are retried).
  - T4 `.github/workflows/analyst.yml` (workflow_dispatch, input `symbol`): stored bars from
    the "bars" release (retried: the nightly save replaces it), no TradingView, no "state"
    concurrency group; `run.py --ci-analyze SYMBOL --archive analyses --daily-limit 10`
    (`analyst/request.py`: limit per UTC day counted by kept folders, failed ones too;
    unknown symbol / limit / failure answered in Telegram in Hebrew); kept on the state
    repo's `analyses` branch (`.github/analyst.sh`, rebase + push; PNGs not kept). Fonts on
    the runner: librsvg2-bin, fonts-noto-core, fonts-ibm-plex (SANS lists Noto Hebrew).
    First run from the phone: OK, 2 minutes. **Owner: a light per section** (green /
    red / yellow for mixed; up always green, down always red; a light may not contradict
    the text's bullish/bearish words) and shorter still (one sentence per section).
  - T5 the bot answers: `tascreen/web/vercel/telegram.js` (copied by export.py to
    `api/telegram.js`): Telegram's secret header = HMAC-SHA256(bot token, "ta-screener
    telegram webhook") (`notify.webhook_secret`, same in JS: no extra secret), owner's chat
    AND user id only, symbol regex only, always 200; dispatches analyst.yml with
    `GH_DISPATCH_TOKEN` (fine-grained key: ta-screener only, Actions read/write) and
    replies "מנתח את X". Keys reach the function as `vercel deploy -e` run-time env from
    run.yml (never stored in the Vercel project). run.yml then runs
    `run.py --telegram-webhook https://ta-screener.vercel.app/api/telegram/` (trailing
    slash: Telegram does not follow the 308) only if GH_DISPATCH_TOKEN exists.
    `--setup-telegram` removes the webhook while it reads getUpdates and puts it back.
    Tested under Node with fake endpoints (tests/test_webhook.py; skipped without Node).
    First live messages got no answer: the keys arrive as pasted (trailing newline), so the
    function trims them; GET /api/telegram/ answers which keys a deployment has (yes/no).
    Works since 2026-09-24 (the owner's messages started analyst.yml runs).
- **Breakout alerts (owner's request 2026-09-24; plan A1 evening report, A2 live watch).**
  Owner's choices: also during the session; a line per stock + full analyses for the
  strongest; all bullish breakouts + stocks on the verge. `tascreen/alerts.py`, config
  section `alerts` (verge_pct 2, watch_pct 5, top_analyses 3, live_interval_minutes 15,
  live_max_symbols 120). Bullish chart patterns only. Sent once: `data/alerts/<session>.json`.
  - A1: `ci_tick` -> `_evening_alerts`: the day's confirmed bullish breakouts, ranked by
    rel_volume then the pattern's hit rate in our ledger (`hit_rates`: patterns with a
    target and >= min_cases decided), + forming bullish/either patterns whose close is
    within verge_pct below `trigger_up`; after a complete update, or anyway 8 h after the
    close (the morning catch-up). The top 3 get analyst.yml via `tascreen/github.py`
    (`dispatch`, GH_DISPATCH_TOKEN). 23/09 would have had 7 breakouts, 22 on the verge.
  - A2: `.github/workflows/live.yml` (crons 13:35 and 14:35 UTC = 09:35 New York in
    summer/winter; group "state"), `run.py --ci-live`: watch list = forming bullish lines
    within watch_pct above the close (~80 stocks), one get-ohlcv (2 daily bars) per stock
    per pass, the newest bar's close if it is today's session; a crossing above
    `trigger_up` (`quotes.crossings`) is sent once, the record pushed at once
    (`push_token_file(path, what)`); a slow pass (median call > 5 s) doubles the interval;
    after 345 minutes it dispatches its own continuation. `state.sh restore` with
    SKIP_BARS=1, `save-alerts`. Not yet run live: measure call times on its first day.
  - **Owner wants it as close to real time as possible; chose TradingView every 5 min**
    (over a Finnhub stream, which would need a new account; its free tier's websocket
    terms were unclear). `live_interval_minutes: 5`; stocks within verge_pct of their line
    every pass, the farther ones every `far_every` (3) passes (`watch_tiers`). Each pass
    also reads the first stock's newest 1-minute bar (`bar_age_minutes`) and the summary
    shows `data_delay_min`: ~1 = real-time data, ~15 = delayed. If it is delayed, a
    faster source (Finnhub/Alpaca stream) is the next step to offer.
  - The evening report adds how the day's intraday crossings closed (held above the
    line / fell back: `intraday_followup`).
- **From a trading video the owner watched (2026-09-25), he chose two additions:**
  - News line per breakout (evening + live): `mcp-tv-get-news` (live schema: symbol, lang,
    limit, offset; answer data.headlines[] with title, published, provider.name,
    relatedSymbols[].symbol, link). Hebrew returned 0 headlines for NVDA: English, as
    published. The newest headline naming the stock and at most 3 stocks, <= 48 h old;
    only TradingView links are linked; a failed call = no headline.
  - `tascreen/backtest.py`, `run.py --backtest` (also nightly after the ledger):
    every decided ledger breakout as a trade: entry next open, exit at the target (scaled
    by the ledger's split `scale`) / the resolved day's close; skipped if resolved before
    entry or the entry is past the target; per pattern+direction: win %, avg/median
    return, profit factor, days, adverse excursion (median / 90th pct), losing streak.
    First run: 32,046 trades 2019-06 .. 2026-09; all bullish: win 47%, avg +2.8%/trade,
    PF 1.44; bearish (shorts) lose (PF 0.58, a bull market). Survivorship: today's list.
    Shown on /scorecard (data/outcomes/backtest.json, statistics only). A running-sum
    drawdown over thousands of overlapping trades was meaningless (thousands of %), so
    the per-trade adverse excursion replaced it.
- **Breakout charts (owner, 2026-09-25: "every bullish breakout with a picture of the
  pattern and the breakout"):** the channels' renderer (`channels/chart_svg.render`) via
  `alerts.pattern_chart`: confirmed breakouts (evening, after the text, albums of 10 via
  `notify.Telegram.send_album` = sendMediaGroup, captions = the report lines, <= 1000
  chars) and live crossings (forming pattern + trigger + live price; live.yml fetches 260
  bars for the crossing stocks only, `alerts.fetch_bars`). A chart that fails never loses
  the alert (text still goes). run.yml and live.yml install librsvg + Noto/Plex fonts.
  `png.svg_to_png` sizes the browser from the SVG's viewBox (the channel charts are
  760x440: no white margins); channel charts clip the averages to the price panel.
- Analyst accuracy pass (owner, 2026-09-25): `view.key_level_facts` (level.s1/s2/r1/r2,
  position, up.*/down.*), the writer must cite up.trigger / down.trigger / level.*; chart:
  header with name/close/change, round axis ticks, close tag, legend, 130 bars, profile
  clipped to the plot; the analyst runner fetches only data/scans/*/indicators.parquet from
  the state repo (sparse, blob-less) for the companies' names.
- **Analyst training loop (plan B, owner 2026-09-25).** `run.py --analyze-eval ROUND`
  (normal terminal: `claude -p`) writes the 12-stock `analyst/evaluate.EVAL_SET` to
  `logs/eval/<ROUND>/`; four review agents (TA expert, beginner, Hebrew editor, trader)
  score it with `analyst/review_rubric.md` (kept out of `knowledge/`, so not in the
  writer's prompt). Round 1 averaged ~5/10. Fixes from it:
  - facts: 52-week high/low (+day, distance), zone distance to the near edge, swing points,
    divergence age, pattern breakout state (`sessions_since_breakout`,
    `close_vs_breakout_pct`, `state`: back inside = doubtful); Fibonacci on the leg in
    progress, none when the move is under `fib_min_atr` ATR / `fib_min_bars`.
  - `view.key_level_facts`: scenario levels from every zone, swing highs/lows and the
    52-week extreme, joined into bands (gap `scenario_min_gap_atr`, width <=
    `scenario_band_max_atr`); `event` by priority (failed breakout, fresh breakout, near
    52-week high/low, 20-day move >= 15%, inside a zone, position).
  - writer: the model writes headline/levels (+ at most 2 optional sections); the two
    scenarios are written by the program (`writer.scenario_parts`); the headline must cite
    `event`, levels must cite `level.*`; forecast wording rejected; prices .2f, no $,
    dates DD/MM; targets are "יעד לפי גובה התבנית (לא תחזית)".
  - chart: tags laid out in price order in the gutter (no overlaps); neutral close tag;
    the 50-day average (cyan, never Fibonacci's gold); a dashed 52-week high/low within
    `extreme_draw_pct` that no zone holds; the outline of a chart pattern whose breakout is
    <= 2 x `event_fresh_sessions` old, with the breakout session marked.
- **Review round 2 (2026-09-25/26):** 12 stocks, 4 reviewers in 3 batches (12 at once hit
  the Claude session limit). Average 5.9/10; the round-1 stocks 5.2 -> 6.0 (novice 5.5->6.4,
  editor 5.6->6.6, trader 4.5->5.5, TA 5.2->5.8). Fixes:
  - facts: 52-week high/low sessions ago, last-day volume ratio, MA direction/slope, stretch
    (>= 3 ATR from the 50-day), MA spread + 6-month range, zone breaks (crossed from the other
    side, held; day, volume), failed divergences dropped, pattern state against the broken
    line carried to today (a retest from the other side is not a failure; past the cancel
    level = failed), breakout-day volume, target already reached, trendline side/crossed.
  - scenarios: next = the next band as a range (never skipping a drawn zone), pattern target
    or Fibonacci extension when nothing is beyond ("not a forecast"), cancel a real level
    >= 1 ATR from the trigger (else >= 0.5), risk % and room % from the trigger; touching
    zones (< 0.4 ATR) join; "closer to" only with a 0.5 ATR difference.
  - events: failed pattern, retest, day-0 breakout ("סגר היום לראשונה"), rejection/bounce at
    a zone today, zone break, new 52-week extreme (also appended to any event), pullback to a
    rising 50-day, range; pattern names as "תבנית ...".
  - writer: new rules (direction words, Latin start, momentum only when extreme, overbought
    is yellow, no repeats, plain words) with checks; $ / "דולר" / this year's dates stripped;
    bold headline, U+200F after the light; scenarios measured from the trigger.
  - chart: drawn after the text (`render(cited=...)`): Fibonacci/profile only if cited, every
    cited chart pattern (window widened to its start); 50/150/200 averages; legend on two
    rows; pattern levels lavender, "ביטול התבנית" only within 3 ATR, no target when forming,
    failed, doubtful or reached; lines from their first touch; broken line dashed to today;
    ▲/▼ breakout marker off the candle, its label only where no candle is; near labels merge;
    half-step axis labels; "נפח מרבי", "פיבונאצ'י"; company name without Inc./(The).
  - scanner (site-wide): a head-and-shoulders neckline must stay on the right side of the
    head and shoulders, and a line extended past the head confirms nothing (a real stock
    "broke out" below both shoulders). Goes into the Saturday backfill with the 10% rule.
- **Review round 3 (2026-09-26):** same 12 stocks and prompts, 4 at a time. Average 6.35
  (round 2: 5.92; the round-1 stocks 5.21 -> 6.03 -> 6.32). Every reviewer's first finding:
  the levels line, the scenarios and the chart used three different lists of levels (the
  scenarios merged zones into unbounded bands the reader never saw). Fixes:
  - one level map (`view._plan`): zones, big turning points, 52-week extremes and the drawn
    trendline join into bands (apart <= 1 ATR, width <= 1.5 ATR, no exceptions). The chart
    and the Pine Script draw the first two bands per side (`lvl_r1`...), the level facts
    (`level.r1.low/high/what/includes/flipped/...`) and the scenarios quote the same bands;
    a scenario level outside them is drawn as its own line or an edge tag (↑/↓) off scale.
    A trendline the price crossed is not drawn.
  - scenarios: cancel = a close back into the crossed band (>= 0.3 ATR wide), else half an
    ATR back from a one-price trigger ("חצי מהתנודה היומית הממוצעת"), never a level past the
    close; next = the next band's own edges, or the nearest measured level (target or
    Fibonacci extension); right after a breakout (<= 5 sessions, within 1.5 ATR of its line)
    "the breakout holds while the close stays beyond the line" (`up.hold` / `down.hold`).
  - the program writes the levels line too (`writer.levels_part`); the model writes only the
    headline (lead in bold, then the event) and <= 2 optional sections. Scenarios in three
    lines, bases named ("רמת ההפעלה", never "רמת הכניסה"); blank line between sections;
    company name in the header.
  - facts in words: volume (`*_volume`, `volume.spike`), `macd.momentum` (from the
    histogram's direction), stretch in percent; bearish statuses "שבירה"; one line per
    pattern (`line_now`, no second `breakout` number); looser uptrend/downtrend call;
    flipped zones "עשויה לשמש"; an intraday new extreme on a day that closed the other way.
  - writer checks: no ATR / תנופה / קו האות / רצועה; no undrawn trendline; "(לא תחזית)"
    stripped (the footer says it); the repeat check ignores MA periods.
  - chart: one label size (two lines instead of shrinking), one number for one price, every
    line with its price, 150-day grey dashed and 200-day light (not three purples), volume
    scale capped at 3x the median, the breakout marker on the side it broke to, "יומי".
  - Not done (need the owner or data): pattern quality in the scanner (triangle touches,
    cup depth), earnings dates, relative strength, gaps, pattern statistics (own ledger only).
- **Flat lines are flat (owner, 2026-09-25): `flat_line_max_drift` 0.25 -> 0.10.** At 0.25 a
  top that rose (NFLX, both lines rising) passed as an ascending triangle and a falling top
  with a rising bottom too. Measured on every stored stock (scratch script, not kept):
  ascending / descending triangles and rectangles 102/157/57 at 0.25, 58/88/17 at 0.15,
  40/52/9 at 0.10; they become wedges and symmetrical triangles; all patterns 1,637 ->
  1,598 (the lost ones: tilted "rectangles" = channels, not in v1). Charts checked by eye:
  0.15 still passed visibly tilted lines; 0.10 kept only flat ones.
  - A rule change renames patterns, so the ledger must not mix definitions:
    `outcomes.update(rebuild=True, rules_digest=...)` (and `--outcomes --rebuild`) takes in
    only scans and backfill rows made with the current rules (other scans are marked read);
    `run_backfill` cuts up to the first scan made with the current rules and rebuilds the
    ledger when it holds rows of other rules.
  - `.github/workflows/backfill.yml`: Saturdays 08:00 UTC (no live watch or daily run then;
    same `state` group). `--backfill-needed` = rules changed since the saved backfill, or no
    finished run with these rules (`logs/backfill_last_run.json`); if yes: bars
    (`state.sh bars`), `--backfill-outcomes`, `--backtest`, save, dispatch run.yml
    (publish), one Telegram line. The step stops at 300 min; the saved per-stock rows let
    the next Saturday resume. First run due 2026-09-26.
- **Tests never reach the real bot**: `tests/conftest.py` clears the Telegram/GitHub keys
  and points `notify.CREDENTIALS` away from ~/.ta-screener (a tick test once sent the
  owner a real report of made-up stocks before that guard existed).

## Plan (user-approved 2026-09-23; stop for review after each phase)

**Decisions:** Bulkowski is the book. v1 filters on chart patterns, candlesticks and
indicators. The site started local only (127.0.0.1); on 2026-09-23 the owner made it
public through a VS Code tunnel (see the website rules above). This is a separate project in a new
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

