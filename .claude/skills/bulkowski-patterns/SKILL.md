---
name: bulkowski-patterns
description: How ta-screener defines and detects Bulkowski chart patterns (double/triple tops and bottoms, head-and-shoulders, triangles, rectangles, wedges, flags, pennants, high-tight flag, cup with handle) and candlestick patterns (hammer, engulfing, stars, harami, soldiers/crows, doji, marubozu, ...). Covers where every threshold lives (tascreen/patterns/rules.yaml), what forming/breakout/busted mean, and how to check a detection against the bars. Use it when explaining a detected pattern, judging whether one is real, changing a threshold, or adding or debugging a detector.
---

# Bulkowski patterns in ta-screener

Answer the user in Hebrew. Code, comments and commits stay in English.

## Where the rules live — read, don't recall

**`tascreen/patterns/rules.yaml` is the only source of thresholds.** Read it before you
state any number. Do not quote thresholds from memory or from the book.

Each parameter has:

- `value`;
- `origin`:
  - `site`: the number is stated on Bulkowski's page for that pattern (thepatternsite.com);
  - `ours`: his guideline is qualitative ("near the same price", "allow variations"),
    and the number is our choice.
- an optional `note`.

`confirmed_from_book: false` means nothing has been checked against the printed books
yet. When you explain a rule, say which kind of number it is.

The repo is public, so it holds:

- only our own wording of the rules;
- no quoted book text;
- none of Bulkowski's statistics (success rates, "percentage meeting price target",
  average moves).

Never add them to committed files.

## What a detection contains

`data/scans/<session>/patterns.parquet`, one row per detection:

- `pattern`: the key in rules.yaml. `family`: `chart` or `candle`.
- `status`:
  - `forming`: complete up to its last turning point, with no breakout yet, and still
    recent enough to be reported;
  - `breakout`: closed beyond the confirmation level or trendline within
    `recent_breakout_sessions`;
  - `busted`: broke out, then closed back beyond the pattern's extreme;
  - `signal`: a candlestick (candles have no breakout concept here).
- `direction`: `bullish` or `bearish`, or `either` for a two-sided pattern (triangle,
  rectangle, wedge, flag) that has not broken out yet.
- `checks_json`: every rule it passed, as `{rule, label_he, value, threshold, passed, origin}`.
- `points_json` and `lines_json`: the turning points and trendlines, for drawing.
- `target`: the **classic full-height measure rule**. Bulkowski scales the height by a
  success percentage from his statistics; that is not used here. A target is a rule of
  thumb, **never a forecast**. Say so whenever you mention one.

## How detection works (so you can explain or debug it)

- Turning points (`pivots.py`) come from a zigzag: a high or low counts once price
  reverses `pivot_atr_multiple` ATRs from it. The newest swing is not a pivot until it
  reverses.
- Double/triple tops and bottoms and head-and-shoulders are fixed sequences of pivots:
  L-H-L, H-L-H-L-H, and so on.
- Head-and-shoulders confirmation follows Bulkowski:
  - a top confirms on a close below an up-sloping neckline, or below the right armpit
    when the neckline slopes down;
  - a bottom is the mirror image.
- Triangles, rectangles and wedges fit least-squares lines through the highs and
  through the lows of 5-9 alternating pivots.
  - A line counts as "flat" when it drifts less than `flat_line_max_drift` of the
    pattern's height.
  - Every pivot must lie within `touch_tolerance_atr` of its line.
  - Triangles and wedges must break out before the apex.
- Flags and pennants: a steep pole, then a short consolidation. The longest valid
  consolidation is tried first, so a half-built flag's next swing is not mistaken for a
  breakout.
- Cup with handle: the "U, not V" test is the share of cup sessions spent in the lower
  third. A straight V spends exactly 1/3 there, which is why `min_bottom_share` > 1/3.
- Candles: tall and small are measured against the average body of the prior
  `avg_body_sessions`. The prior trend is the close before the pattern against
  `trend_sessions` earlier.
- When detections overlap (two or more shared turning points, same direction), the
  larger formation wins.

## Checking one detection

1. Print it with its checklist:
   `.venv\Scripts\python.exe run.py --scan-symbol NASDAQ:XXXX`
2. Re-derive it from raw bars, independently. Use the stored
   `data/bars/<EXCHANGE_TICKER>.parquet`, or `mcp__tradingview__mcp-tv-get-ohlcv` for
   a fresh copy.
3. For every rule in the pattern's `rules_he`, measure the value yourself and compare
   it with the threshold in rules.yaml.
4. Prefer a second opinion: the `pattern-verifier` agent does exactly this.

## Changing a threshold

- Edit rules.yaml. Keep `origin` honest: if Bulkowski's page does not state the number,
  it is `ours`.
- Run `.venv\Scripts\python.exe -m pytest -q`. The synthetic textbook shapes in
  `tests/test_chart_patterns.py` and `tests/test_candles.py` must still pass.
- Rerun `run.py --scan` (no TradingView calls), and tell the user how the counts moved.
