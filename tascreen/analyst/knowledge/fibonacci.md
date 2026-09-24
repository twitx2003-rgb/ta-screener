# Fibonacci retracements (facts fib.*, fib_NNN, fib_ext_NNNN)

Sources (ideas, in our own words): StockCharts ChartSchool, "Fibonacci Retracements",
https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-annotation-tools/fibonacci-retracements

What the engine computes:
- The last major swing: a zigzag that flips only after a 4 ATR move. fib.start / fib.end
  are its prices, fib.start_day / fib.end_day its dates, fib.direction up or down.
- Retracement levels of that swing: 23.6%, 38.2%, 50%, 61.8%, 78.6% (fib_236 ... fib_786),
  and extensions 127.2% and 161.8% (fib_ext_1272, fib_ext_1618).
- fib.retraced_pct: how much of the swing the price has already given back.
- The chart shows only 38.2 / 50 / 61.8, and only while the price is inside a pullback
  (retraced between 20% and 85%). A level that falls on a support/resistance zone is written
  on that zone's label: two methods pointing at the same area (confluence).

How to read it:
- 38.2% and 61.8% are the most watched levels; 50% is not a Fibonacci ratio but is
  commonly used with them.
- A retracement level is where a pullback may pause; it carries more weight when it
  coincides with a zone or with the POC. Never present it as a place the price will stop.
- If the price is beyond the swing (retraced below 0% or above 100%), the retracement
  levels matter less; say so briefly or skip the section.

Hebrew terms: פיבונאצ'י, רמת תיקון, תיקון של X% מהתנועה, חפיפה (confluence).
