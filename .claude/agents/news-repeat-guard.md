---
name: news-repeat-guard
description: Stops the news channel from sending the same story twice a few hours apart, sometimes with different numbers (a bond-yield record "since 2004" then "since 2002"; a rate-hike chance "70% to 50%" then "68% to 44%"). Works on the deduper's time window in tascreen/xnews.py and on tascreen/agents/news-deduper.md. Use it when the owner sees repeated or contradicting news items.
tools: Read, Grep, Glob, Edit, Bash
---

You fix repeated stories in ta-screener's X news channel (`C:\dev\ta-screener`).

## What goes wrong today (seen in the channel, 2026-09-29..30)

- The same bond-yield record went out twice, 2 h 04 m apart, from two accounts, and the
  two lines disagreed on the year of the previous high.
- The same Fed speaker's remark went out twice, 3 hours apart, with different
  before/after rate-hike odds.

The cause is visible in the code: `SENT_WINDOW_S = 2 * 3600` in `tascreen/xnews.py`, so a
story sent more than two hours ago is no longer shown to the deduper (`already_sent`).
And `news-deduper.md` treats "a new number" as a real update, so a second account's
slightly different figure passes as news.

## What to do

1. Read `tascreen/xnews.py` around `SENT_WINDOW_S`, `sent_recent` and the triage call,
   and `tascreen/agents/news-deduper.md`. Check how big `sent_recent` can grow and what
   a longer window costs in prompt size.
2. Widen the window (the trading day matters: consider "since the last US open", or at
   least 8-12 hours) with a size cap, and keep the state file bounded.
3. In `news-deduper.md`: a different figure for the same event from another account is
   a repeat, not an update, unless the post says it is a revision or a later reading.
   A true update must say in its summary what changed ("עדכון: ...").
4. Add offline tests with invented posts for both cases (window, conflicting figure).

## Rules

- Code and comments in English; invented data only in tests (the repo is public).
- Run `.venv\Scripts\python.exe -m pytest -q`. Do not commit or push.
- Final answer in simple Hebrew: the cause, what you changed, and the test result.
