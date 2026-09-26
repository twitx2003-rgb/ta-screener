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
.venv\Scripts\python.exe run.py --serve               # website on http://127.0.0.1:8050/ (opens the browser)
.venv\Scripts\python.exe run.py --quotes              # every stock's last price once -> data/quotes/
.venv\Scripts\python.exe run.py --live                # quotes every 5 min in session, daily update after close
.venv\Scripts\python.exe run.py --channels            # agents write today's channels (normal terminal only)
.venv\Scripts\python.exe run.py --channels --force    # write them again
.venv\Scripts\python.exe run.py --redraw-charts      # redraw stored chart posts after a chart_svg change
.venv\Scripts\python.exe run.py --outcomes           # breakout ledger from the saved scans (also after every scan)
.venv\Scripts\python.exe run.py --outcomes --rebuild # rebuild it from the saved scans (+ backfill) made with the current rules
.venv\Scripts\python.exe run.py --backfill-outcomes [--limit N]  # past breakouts, no look-ahead (~1.5 h, resumable)
.venv\Scripts\python.exe run.py --backfill-needed    # yes/no: rules changed since the backfill (backfill.yml)
.venv\Scripts\python.exe run.py --analyze NVDA --no-llm    # facts, chart, Pine Script -> logs/analyses/
.venv\Scripts\python.exe run.py --analyze NVDA --telegram  # + Hebrew text, sent to the bot (normal terminal only)
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
- **The website (`tascreen/web/`) only reads** data/ and logs/; it never calls TradingView.
  - Filters live in the URL (`web/filters.py`); a bad value is dropped and reported in
    Hebrew, never guessed.
  - No raw nan/None/NaT on a page (`fmt.page_problems`, asserted in `tests/test_web.py`).
  - RTL: numbers in `num` spans (LTR), English names isolated (`.co`), numeric inputs LTR
    with digit-only placeholders. Hebrew labels live in `web/labels.py`.
  - Lightweight Charts 5.2.1 is vendored: keep its NOTICE line and `attributionLogo`.
  - Design: dark by default, purple accent, Heebo + IBM Plex Mono. The ui-ux-pro-max audit
    of 2026-09-26 is the bar for new UI: contrast >= 4.5:1, no text under 12 px, 44 px
    touch targets, a skip link, line icons (no emoji as icons).

## How it runs (details per subsystem: docs/NOTES.md)

- Hosting: GitHub Actions + Vercel (https://ta-screener.vercel.app); the home PC runs
  nothing. Workflows share the concurrency group `state` (a newer queued run cancels a
  pending one):
  - `run.yml`: nightly 21:40 UTC Mon-Fri, catch-ups 01:10 / 05:10 UTC Tue-Sat (update,
    scan, channels, alerts, publish);
  - `live.yml`: quotes during the session;
  - `analyst.yml`: one analysis on request (the Telegram bot dispatches it);
  - `backfill.yml`: Saturdays 08:00 UTC, when the pattern rules changed;
  - `eval.yml`: an analyst evaluation round (outside the `state` group).
- State: the PRIVATE repo `twitx2003-rgb/ta-screener-state` (TradingView sign-in, data
  minus bars, full logs; bars in its `bars` release; branches `analyses` and `eval`).
- Secrets: STATE_REPO_TOKEN, CLAUDE_CODE_OAUTH_TOKEN, VERCEL_TOKEN, TELEGRAM_BOT_TOKEN,
  TELEGRAM_CHAT_ID, GH_DISPATCH_TOKEN. Never print them.
- This repo's Actions logs are public: print counts, dates and error class names only;
  everything else goes to the state repo. Workflow inputs go through `env:`, never
  `${{ }}` inside `run:`.
- `gh` is not installed locally. The GitHub API works with curl and the stored git
  credential (`git credential fill`), never printed.
- `claude -p` refuses to run inside Claude Code (CLAUDECODE=1): never bypass it. Model work
  runs in the workflows (analyst.yml, eval.yml) or in the owner's normal terminal.
- Tests never reach the real bot: `tests/conftest.py` clears the Telegram/GitHub keys.

## Current work (history: docs/HISTORY.md)

- Chart analyst (`tascreen/analyst/`): facts -> one level map (`view._plan`) -> text
  (`writer.py`: the model writes the headline and at most two sections, the program writes
  the levels line and the scenarios) -> chart (`chart.py`) -> Telegram.
- Training loop (plan B): `eval.yml` runs the 12-stock set (`--analyze-eval ROUND --until
  2026-09-23`, so every round reads the same charts) onto the state repo's `eval` branch.
  Four review lenses (TA expert, novice, Hebrew editor, trader; `analyst/review_rubric.md`),
  four agents at a time (twelve at once hit the session limit). Scores: round 1 5.21 (its 4
  stocks), round 2 5.92, round 3 6.35; target 8.5. Next: B2, a reviewer inside writer.py.
- Open: pattern quality in the scanner (triangle touches, cup depth: discuss first, it
  changes the whole site), earnings dates, relative strength, gaps; the hosting keepalive
  before about 2026-11-23.

## Working rules

- Stop for review after each stage. Discuss site-wide scanner changes with the owner first.
- Public repo: synthetic values in tests; no vendor numbers in comments or commit messages.
- Edits that contain backslashes: write a Python script with the Write tool (Bash heredocs
  here collapse double backslashes into one).
- Long background notes live in docs/NOTES.md and docs/HISTORY.md: read the part you need,
  not the whole file (review round 3, 2026-09-26: this file was 876 lines, loaded into
  every conversation and every helper agent).

## Safety

- Never print, log or commit tokens (`~/.ta-screener/tv_tokens.json`) or `.env`.
- Never commit or upload `data/` or `logs/`. The server binds to 127.0.0.1 only.
  Exposing it is the owner's own step, through their VS Code tunnel; Claude does not
  open tunnels.
- Nothing here is investment advice. Pattern targets are the book's measure rule, not
  forecasts.
