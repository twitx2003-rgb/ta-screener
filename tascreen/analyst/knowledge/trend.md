# Trend: moving averages and trendlines (facts sma*, vs_sma*, ma.stack, tl_N.*, change_20d_pct)

Sources (ideas, in our own words): StockCharts ChartSchool, "Moving Averages - Simple and
Exponential",
https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential ;
"Trend Lines", https://chartschool.stockcharts.com/table-of-contents/chart-analysis/trend-lines

What the engine computes:
- Simple moving averages of the close over 50, 150 and 200 sessions (sma50, sma150,
  sma200) and the price's distance from each in percent (vs_sma50_pct, ...).
- ma.stack: bullish when price > 50 > 150 > 200, bearish when price < 50 < 150 < 200,
  otherwise mixed.
- Trendlines through at least 3 turning points, unbroken (no close beyond by more than
  0.5 ATR), last touched in the last 40 sessions, and within 6 ATR of the price.
  tl_N.value is the line's value on the last day, tl_N.direction rising / falling / flat.
- change_20d_pct: the price change over the last 20 sessions.
- sma{50,150,200}.direction and .slope_pct: each average rising, falling or flat over
  the last 10 sessions. Say an average rises only when its direction fact says so.
- ma.price_vs: the price above all three averages, below all three, or between them.
- sma50.distance_atr and stretch: how far the price is from the 50-day average in ATRs;
  from 3 ATRs the price is stretched (stretch).
- ma.spread_pct and range.*: the three averages bunched within 2% of each other mean no
  trend; range.low / range.high are the lowest low and highest high of about six months.

How to read it:
- The order of the averages describes the trend: all stacked upward is an uptrend, all
  downward a downtrend, mixed is a transition or a range.
- A long average (150/200) is slow: the price above or below it describes the longer-term
  trend; the 50 describes the recent one.
- A trendline with more touches is more established; a close through it is a warning
  sign, not a certain reversal.
- Describe a trend only as the cited facts show it (ma.stack, tl_N.direction).
- A stretched price (stretch) came far fast: it often pauses or pulls back toward the
  average; it is a poor place to start a position, not a signal to sell. Light: yellow.
- In an uptrend, a pullback to a rising 50-day average is a common test of the trend.
- A trendline is above or below the price: always say which (tl_N.distance_pct: minus
  means the line is under the price). A line the price has crossed is crossed, not a level
  on the other side.
- Plain words: "מגמת עלייה" / "מגמת ירידה" / "אין מגמה ברורה", "הממוצעים של 50, 150
  ו-200 יום"; not "סדר הממוצעים מעורב".

Hebrew terms: מגמה עולה, מגמה יורדת, דשדוש, ממוצע נע, קו מגמה.
