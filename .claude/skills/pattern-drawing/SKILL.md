---
name: pattern-drawing
description: How ta-screener should draw every chart pattern and its breakout so a reader sees the pattern at a glance on a phone - which lines, points, shading, measure bracket and labels each pattern gets (double/triple tops and bottoms, head-and-shoulders, triangles, rectangle, wedges, flag, pennant, high-tight flag, cup with handle), the accuracy rules a drawing must pass against the bars, and the label/colour budget. Use it when changing tascreen/chart_svg.py or tascreen/analyst/chart.py, when auditing a breakout picture, or when the owner says a chart is cluttered or unclear.
---

# Drawing patterns and breakouts

Answer the owner in Hebrew. Code, comments and commits stay in English.

The owner's complaint (2026-10-01): the analyses are heavy with text and the owner "cannot see
what you are talking about". The picture must carry the story; the text only points at
it. A reader who sees only the picture should be able to say: which pattern, where it
broke, where it aims, where it fails.

Sources (rules restated in our own words; never copy book text or statistics into the
repo, it is public): each pattern's `url` in `tascreen/patterns/rules.yaml`
(thepatternsite.com), e.g. https://www.thepatternsite.com/at.html (the breakout price is
where price pierces the trendline; the height is measured inside the pattern and added
to the breakout price), https://www.thepatternsite.com/cup.html (the rim line, a
down-sloping line along the handle). Design: direct labels instead of legends, remove
ink that carries no information (Tufte's data-ink; NN/g "clutter charts").

## 1. Accuracy rules — a drawing that breaks one is wrong, however pretty

1. **Draw only the detection's geometry.** Points, lines, breakout date, breakout price,
   target and invalidation come from the detection record (`tascreen/patterns/chart.py`,
   `levels.invalidation`). The renderer never recomputes or "improves" them. If the
   geometry looks wrong, the bug is in the detector: report it, do not paint over it.
2. **A turning point sits on its bar's extreme**: a peak on that bar's high, a valley on
   its low. A marker floating off the candle is a bug.
3. **A line passes through its touches.** A touch is within the tolerance in
   `rules.yaml` (`general`); a close beyond a line before the breakout means the pattern
   is invalid, not that the line should be bent.
4. **The breakout is a close, not a wick**, on a finished session (after 16:15 New York,
   `market_hours`). It is the first such close beyond the line. For a sloped line, the
   line's value is evaluated **on the breakout bar**, not at its last touch.
5. **Mark the breakout where it happened**: a ringed dot at (breakout bar, line value on
   that bar), and the breakout candle outlined. Not a floating tag 60 px away.
6. **A live (intraday) crossing is never drawn as a breakout.** It is "חי" with the live
   price, and the caption says it is not final until the close.
7. **Lines end at the breakout bar.** A horizontal trigger line may continue to the last
   bar as a faint dashed "retest" line; sloped lines never run past the breakout.
8. **The target is the measure rule, shown as a measure:** a vertical bracket for the
   pattern's height inside the pattern, and the same length projected from the breakout
   point to the target. The reader sees why the target is there. The label says "יעד",
   never "תחזית".
9. **Invalidation** is the level `levels.invalidation` returns, drawn from the breakout
   (or the pattern's end) to the right edge, dotted, in the opposite direction's colour.
10. **Busted / failed**: if price closed back through the line after the breakout, the
    picture shows it ("כשל" at that bar) and never shows a live target.
11. **Window:** the pattern takes at least half the width; keep enough bars before it to
    show the trend into it (flags need the whole pole, reversals need the prior trend).
    Every drawn level is inside the price scale (no target cut off at the top).

## 2. Clean-design rules (phone first)

- **One story per picture**: pattern, trigger line, breakout, target, invalidation.
  Moving averages, the volume box and zones stay off unless the text refers to them.
- **Label budget: at most 5 tags** in the price area. Prices of target, invalidation and
  the live price go into **tags on the right price axis**, coloured by role; the
  in-chart label is a word, not a sentence ("יעד", "ביטול", "פריצה 21/09").
- **Pivot names only where they teach the shape**: head-and-shoulders (כתף, ראש, כתף).
  Elsewhere small numbered dots (1, 2, 3) or plain dots.
- **Shade the pattern's area** lightly (fill-opacity about 0.10-0.14): the triangle or
  wedge polygon between its lines, the rectangle box, the flag/pennant channel, the cup.
  A shape you can see at thumbnail size beats five labels.
- **Colour roles, fixed in every chart** (both renderers):
  - structure lines and dots: one neutral (lilac `ANN["line"]`), stroke >= 2;
  - trigger / confirmation line: thicker (about 2.8), switches to the direction colour
    once broken (green up, red down);
  - target: amber dashed + bracket; invalidation: dotted, opposite colour;
  - live price: a pulsing-style ring, green.
- **No overlaps:** tags never cover candles of the pattern or each other; when two tags
  collide, move one along its line or drop the less important one. Never stack boxes.
- Fonts: tags >= 13 px at the 760 px base (the PNG is x2). Hebrew text in tags is RTL;
  numbers stay LTR inside it (use the existing `_ltr` helpers).
- Volume: plain bars; highlight only the breakout bar's volume (outline) when it is
  above its 50-day average. No dashed box around the whole pattern.

## 3. Per pattern: what to draw

`dir` = the breakout's direction. Every pattern also gets: the breakout dot and outlined
candle (rule 5), the measure bracket and target (rule 8), invalidation (rule 9).

| Pattern (rules.yaml key) | Structure | Trigger line | Shade | Height for the bracket |
|---|---|---|---|---|
| double_bottom / double_top | dots "1", "2" on the two valleys (peaks) | horizontal at the middle peak (valley), from it to the breakout | none, or a light band between the bottoms and the line | lower bottom (higher top) to the line |
| triple_bottom / triple_top | dots 1-3 | horizontal at the highest peak (lowest valley) between them | as above | as above |
| head_shoulders_top / _bottom | "כתף", "ראש", "כתף" on the three extremes; two small dots on the armpits | the neckline through the armpits, may slope, to the breakout | none | head to the neckline measured **at the head's date** |
| ascending / descending / symmetrical_triangle | small dots on each touch | the side that broke (bold); the other side thin | the polygon between the two lines | the widest part (the pattern's start) |
| rectangle | dots on touches | the broken side | the box | box height |
| rising_wedge / falling_wedge | dots on touches | the broken side | the polygon | none: the target is the wedge's start (highest high / lowest low), drawn as a level with the word "תחילת הטריז" |
| flag / pennant | the pole as one thick segment from its start to its top, labelled "תורן" | the flag's broken side | the channel (flag: parallel; pennant: converging) | the pole's height, bracket drawn on the pole |
| high_tight_flag | pole as above | horizontal at the flag's high | the flag area | the pole |
| cup_with_handle | a smooth arc through left rim, bottom, right rim (a path through smoothed lows, not three dots), the handle as a small down-sloping line or box | the rim line, horizontal at the right rim | the cup, very light | rim to the cup's bottom |

Forming patterns (no breakout yet): the same structure and shading, the trigger line
dashed and extended to the last bar with "פריצה מעל 112.30" on the price axis; no
target bracket until there is a breakout.

## 4. The analysis message (`$SYMBOL`): picture first, text second

- Every level the text names is on the chart with the **same word** (and the same
  letter/number tag if several); a level that is not on the chart is not in the text.
- If an active or recently broken pattern exists, the analysis chart draws it with the
  same per-pattern rules (one renderer for both, or shared helpers), and the zones are
  at most two per side, never covering the pattern.
- Text budget: a headline of one sentence, at most three short lines of facts, and the
  two scenarios as one line each ("מעל 104.20 → 112.60 · ביטול מתחת 101.80"). The
  explanation of a term goes on the chart once, not in every message.

## 5. How to look at a drawing

Render, then **look at the PNG** (Read on the .png shows it). Tests only prove the SVG
is well-formed. A local preview script (gitignored, `logs/preview/`) can render every
pattern from the synthetic shapes in `tests/test_chart_patterns.py` / `tests/synth.py`
and the real newest scan in `data/scans/` (never commit those pictures: vendor prices).
`tascreen/analyst/png.svg_to_png(svg_path, png_path)` turns an SVG into a PNG with the
local Chrome.
