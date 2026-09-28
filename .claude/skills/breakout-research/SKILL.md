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
- **Live crossings during the session stay as they were:** immediate, no research.
- Keep Claude's share small: the team works in batches (four calls a day).

## The steps

1. **Dossier per candidate (code, `research.py`):** the analyst engine's facts
   (`analyst/facts.analyse`), the detection's checklist, the pattern's hit rate
   (`alerts.hit_rates`, the outcome ledger) and backtest, 3-month relative strength
   against the whole list and the sector, days to earnings, TradingView headlines
   (`alerts.fetch_news`). No model invents a number: every number in the text must be in
   the dossier (`analyst/text_rules`), and advice or forecast wording is dropped.
2. **Shortlist (code):** at most 8 by a transparent score.
3. **Three specialists, one call each over the whole shortlist:** `pattern-auditor`
   (is it the textbook pattern, breakout quality), `context-analyst` (market, sector,
   news, earnings), `statistician` (what similar setups did in our ledger).
4. **`chief-strategist`:** at most 3 picks, or none; for each, why, what cancels it and
   what to watch next session.
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
