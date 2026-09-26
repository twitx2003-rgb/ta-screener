# Support and resistance zones (facts zone_N.*)

Sources (ideas, in our own words): StockCharts ChartSchool, "Support & Resistance",
https://chartschool.stockcharts.com/table-of-contents/chart-analysis/support-and-resistance

What the engine computes:
- Turning points: a zigzag over the daily bars that flips after a move of 1.5 ATR
  (ATR = the average daily range of the last 14 sessions).
- A zone is a cluster of at least 2 turning points whose prices lie within 0.6 ATR of
  each other; a zone is never wider than 1 ATR. zone_N.low / zone_N.high are its edges,
  zone_N.touches the number of turning points in it.
- A zone above the last close is resistance (התנגדות), one below is support (תמיכה).
  Only the 3 nearest on each side are kept; the chart draws the 2 nearest.
- zone_N.distance_pct and level.*.distance_pct: how far the zone's NEAR edge is from the
  close, in percent (0 when the price is inside it). Say which edge: "הקצה הקרוב".
- The level map (level.r1 / level.r2 above, level.s1 / level.s2 below): the zones, the big
  turning points, the 52-week extremes and the drawn trendline, nearest first; levels
  within 1 ATR of each other join one band while it stays within 1.5 ATR. level.*.what
  names it, level.*.includes says what else sits in it (the yearly extreme, a trendline,
  an average, a pattern's line), level.*.flipped a role it may have taken after a break.
  The chart, the levels line and the scenarios all use these same bands; the program
  writes the levels line and the scenarios, so a section never repeats their numbers.
- zone_N.broken (with broken_day, broken_sessions_ago, broken_volume): the price
  closed through the zone lately, coming from the other side, and has stayed beyond it:
  a resistance broken upward now sits under the price, a support broken downward above it.

How to read it:
- A zone is an area, not an exact line: give its range ("בין X ל-Y"); never a dash
  range in the text, it can flip in right-to-left text.
- Say "ההתנגדות הקרובה" / "התמיכה הקרובה" and "האזור הבא"; always "אזור", never "רצועה".
- More touches means the level was tested more times, not that it is certain to hold.
- A level that breaks often switches role: broken support can act as resistance later and
  broken resistance as support. Say this as a possibility ("עשויה"), never as a certainty.
- A close beyond a zone matters more than an intraday wick through it.
- The nearest zones matter most for the coming sessions; far zones are context.

Hebrew terms: תמיכה, התנגדות, אזור, נגיעות, פריצה (a close above resistance),
שבירה (a close below support).
