---
name: breakout-research
description: The breakout research team in ta-screener (tascreen/research.py, tascreen/agents/pattern-auditor.md, context-analyst.md, statistician.md, chief-strategist.md, learning-coach.md) - how the evening's candidates are researched so the owner gets only THE BEST setups, what each role sees, how picks are checked, recorded and learned from, and how to judge the team. Use it for any request about which breakouts reach the owner, the research agents, their picks, lessons or track record.
---

# The breakout research team

Talk to the owner in simple Hebrew. Code, comments and commits stay in English.

## What the owner asked for (2026-09-28)

- The team does deep research **to bring the owner THE BEST setups**, not an analysis of
  every breakout. Only what passes the research is sent.
- Candidates: the day's confirmed bullish breakouts and the forming patterns on the verge
  (`alerts.bullish_breakouts`, `alerts.on_the_verge`).
- **The owner's own watchlist (2026-09-30):** `watchlist.id` in config.yaml (an opaque TradingView
  id; the symbols live only in the private state repo, `data/watchlist.json`, read once a
  trading day by the live watch). Its stocks are priced every pass; any of their patterns
  crossing a line (no record gate), and a move of `watchlist.move_pct` (4%) from the last
  close, then each further 4%, go to the owner's PRIVATE chat (`notify.owner_from_environment`),
  never to the group; X news about them that the group did not get goes there too.
- **Live crossings during the session:** immediate, no research, but picked in code
  (owner, 2026-09-28): only proven patterns (the same `proven_patterns` gate), the price
  at least `live_min_above_pct` (0.5%) above the line, at most `live_per_pattern` (2)
  of one pattern a session. Stocks above $5B only (`universe.min_market_cap`).
- Keep Claude's share small: the team works in batches (four calls a day).

## The steps

1. **Dossier per candidate (code, `research.py`):** the analyst engine's facts
   (`analyst/facts.analyse`), the detection's checklist, the pattern's hit rate
   (`alerts.hit_rates`, the outcome ledger) and backtest, 3-month relative strength
   against the whole list and the sector, days to earnings, TradingView headlines
   (`alerts.fetch_news`). No model invents a number: every number in the text must be in
   the dossier (`analyst/text_rules`), and advice or forecast wording is dropped.
2. **Shortlist (code):** only patterns with a proven record (`alerts.proven_patterns`:
   at least `alerts.min_success_pct`, 35%, of their ended bullish breakouts reached the
   target, failed and expired counting against); at most 8 by a transparent score, at
   most 2 of one pattern (owner, 2026-09-28: "mostly wedges", wants variety and meaning).
3. **Three specialists, one call each over the whole shortlist:** `pattern-auditor`
   (is it the textbook pattern, breakout quality), `context-analyst` (market, sector,
   news, earnings), `statistician` (what similar setups did in our ledger).
4. **`chief-strategist`:** up to 10 picks (owner, 2026-09-30; it was 3), or none, at most
   two per pattern; the candidates include setups up to `watch_pct` (5%) below the line; for each, why,
   what cancels it and what to watch next session.
5. **Evening message:** only the picks (chart, reasons, the full analysis via
   `analyst.yml`), and "נבדקו N, נבחרו K". No pick: a short note. Research failed:
   the old report with a note, so a day is never lost.

## Learning

- Every pick is recorded in `data/research/picks.parquet` (PRIVATE state repo).
- The outcome ledger (`outcomes.py`) decides target / failed / expired.
- Weekly, code compares picks with outcomes; `learning-coach` writes at most 10 lessons
  to `data/research/lessons.md` (private), which the specialists read from then on.
  The owner gets a short weekly track record.

## Judging the team

Precision of the picks (reached target before failure) against all breakouts of the same
days is the measure; a lesson must name the evidence (counts from our ledger). Never
present a pick as advice: a target is the pattern's measure rule, not a forecast.

## Checking a change

`pytest -q` (SyntheticLLM); `run.py --research-dry DAY` in GitHub or the owner's normal
terminal prints the picks without sending. Claude cannot run inside Claude Code.
