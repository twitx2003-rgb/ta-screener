# CLAUDE.md — project brief for Claude Code

A Hebrew Telegram bot that screens US stocks above a $5B market cap (was $1B until
2026-09-28) and a 10-day average volume of 1M shares a day (2026-10-05, `universe.min_avg_volume`:
the scan, reports, live crossings, pre-market movers and the setups list) by technical
analysis (the website was removed on 2026-09-27: the owner uses Telegram only). It looks at:

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
.venv\Scripts\python.exe run.py --export-webhook DIR  # what Vercel serves: the bot's webhook only
.venv\Scripts\python.exe run.py --outcomes           # breakout ledger from the saved scans (also after every scan)
.venv\Scripts\python.exe run.py --outcomes --rebuild # rebuild it from the saved scans (+ backfill) made with the current rules
.venv\Scripts\python.exe run.py --backfill-outcomes [--limit N]  # past breakouts, no look-ahead (~1.5 h, resumable)
.venv\Scripts\python.exe run.py --backfill-needed    # yes/no: rules changed since the backfill (backfill.yml)
.venv\Scripts\python.exe run.py --analyze NVDA --no-llm    # facts, chart, Pine Script -> logs/analyses/
.venv\Scripts\python.exe run.py --analyze NVDA --telegram  # + Hebrew text, sent to the bot (normal terminal only)
.venv\Scripts\python.exe run.py --setup-x              # save the twitterapi.io key (asked for, never shown)
.venv\Scripts\python.exe run.py --x-discover Reuters   # real answer's keys: confirm xnews.py fields
.venv\Scripts\python.exe run.py --xnews                # one X news pass -> Telegram (normal terminal only)
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
  One exception (owner, 2026-10-02): `OwnListSession` may add to / remove from the bot's
  own setups list (`setups_list.id`) and nothing else; Claude Code sessions stay denied.
- **Never guess a field.** Payload fields go through `tascreen.fields.pick()`. Screener
  columns come from the live catalogue (`--discover`), never from memory.
- Every stored frame is checked against a contract in `tascreen/contracts.py`.
- A daily bar is a close only after `market.session_close` (16:15 New York); see
  `tascreen/market_hours.py`.
- Config is in `config.yaml`. Unknown sections and keys are rejected.
- What is left of `tascreen/web/`: `data.py` (the newest scan as a view, read by the
  alerts), `labels.py` (sector names) and the bot's webhook (`vercel/telegram.js`, deployed
  alone by `export.py`). Links in messages go to TradingView's chart, not to a site.

## How it runs (details per subsystem: docs/NOTES.md)

- Hosting: GitHub Actions + Vercel (only the bot's webhook, ta-screener.vercel.app/api/telegram/);
  the home PC runs
  nothing. Workflows share the concurrency group `state` (a newer queued run cancels a
  pending one):
  - `run.yml`: nightly 21:40 UTC Mon-Fri, catch-ups 01:10 / 05:10 UTC Tue-Sat (update,
    scan, alerts, webhook deploy);
  - `live.yml`: from 07:25 New York, the pre-market reports (07:30, 08:30, 09:15;
    `tascreen/premarket.py`, with the market explainer `tascreen/explain.py`), then breakout
    crossings and sharp index moves during the session (Telegram);
  - `xnews.yml`: one looping run (5.5 h, then it dispatches its continuation; GitHub cron is
    unreliable): X news every 10 min 04-24 UTC (20 on weekends), and `--ensure-live`
    starts live.yml if it is missing (`tascreen/xnews.py`;
    own group `xnews`, seen ids on the state repo's `xnews` branch);
  - `analyst.yml`: one analysis on request (the Telegram bot dispatches it);
  - the evening report is the research team's (`tascreen/research.py`, `--research-dry` to try;
    weekly review on Saturday runs); if it fails, the regular report goes with a note;
  - `backfill.yml`: Saturdays 08:00 UTC, when the pattern rules changed;
  - `eval.yml`: an analyst evaluation round (outside the `state` group).
- State: the PRIVATE repo `twitx2003-rgb/ta-screener-state` (TradingView sign-in, data
  minus bars, full logs; bars in its `bars` release; branches `analyses` and `eval`).
- Secrets: X_API_KEY (twitterapi.io), STATE_REPO_TOKEN, CLAUDE_CODE_OAUTH_TOKEN, VERCEL_TOKEN, TELEGRAM_BOT_TOKEN,
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
  The exact reviewer prompts, the steps and each round's scores are local, in
  `logs/eval/REVIEW.md` and `logs/eval/roundN/scores.md` (gitignored: they quote prices).
- Breakout pictures (2026-10-01): `chart_svg.py` draws each pattern per
  `.claude/skills/pattern-drawing/SKILL.md` (agents: breakout-drawing-auditor,
  pattern-chart-designer, analysis-visual-editor). Flags: lines on the extremes, only
  pole-direction breaks; high-tight flag gets a pole-height target; doubles, triples and
  H&S must break out within their own length (`max_breakout_wait_share`). Next: the same
  drawing and a shorter text in the `$SYMBOL` analysis.
- Support tests (2026-10-05, `analyst/support.py`, `knowledge/support.md`): the 20/150-day
  averages as support (holds, breaks, strength with the bounce's volume not weak), the latest
  close across each, and retests after pattern/zone/150-day breakouts. A program-written
  "בדיקות תמיכה" line in every analysis (`view.support_lines`), key events, the 20-day on the
  chart when named, and the research team's prompts.
- Setups list (2026-10-02, `tascreen/setups_list.py`): the bot's own TradingView list,
  refreshed nightly after the evening report. Since 2026-10-05 the owner's setups only (no
  research picks): the analyst over every followed stock; in an uptrend, a 150-day hold, a
  strong 20-day hold, a breakout retest that held (bounce >= 1.15x volume), or a fresh
  breakout (>= 1.5x); no wedges. Out: a close through the level, a target, 10 sessions
  without renewal, or a stock no longer followed. At least 10 (2026-10-06: filled on volume
  that is not weak, up to 5 sessions back); 15 minutes after the open the live watch takes
  out what opened under its level and refills from last night's pool. Private chat.
  Also scanned (2026-10-06): the US stocks and funds on the owner's own TradingView lists
  (`owner_lists`, under the volume floor; entries marked `from_lists`).
- Foreign companies (2026-10-06): TradingView types ADRs "dr" (TSM, ASML, ARM), which the
  "stock" query never returned; `universe.depositary_receipts` fetches them by a query of
  their own (fail-safe: the stocks go on alone if it fails).
- Open: pattern quality in the scanner (triangle touches, cup depth: discuss first, it
  changes every alert), earnings dates, relative strength, gaps; the hosting keepalive
  before about 2026-11-23.

## Working rules

- Stop for review after each stage. Discuss scanner-wide changes with the owner first.
- Public repo: synthetic values in tests; no vendor numbers in comments or commit messages.
- Edits that contain backslashes: write a Python script with the Write tool (Bash heredocs
  here collapse double backslashes into one).
- Long background notes live in docs/NOTES.md and docs/HISTORY.md: read the part you need,
  not the whole file (review round 3, 2026-09-26: this file was 876 lines, loaded into
  every conversation and every helper agent).

## Safety

- Never print, log or commit tokens (`~/.ta-screener/tv_tokens.json`) or `.env`.
- Never commit or upload `data/` or `logs/`.
- Nothing here is investment advice. Pattern targets are the book's measure rule, not
  forecasts.
