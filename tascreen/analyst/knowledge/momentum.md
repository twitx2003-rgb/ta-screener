# Momentum: RSI, MACD and divergences (facts rsi14, rsi.zone, macd*, div_N.*)

Sources (ideas, in our own words): StockCharts ChartSchool, "Relative Strength Index (RSI)",
https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi ;
"MACD",
https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/macd-moving-average-convergence-divergence-oscillator

What the engine computes:
- RSI over 14 sessions (Wilder's smoothing). rsi.zone: above 70 overbought (קניית יתר),
  below 30 oversold (מכירת יתר), otherwise neutral.
- MACD 12/26/9: macd (fast minus slow average), macd.signal (its 9-session average),
  macd.hist (the difference); macd.state above/below the signal line, macd.zero above/below 0.
- Divergences: two consecutive turning points of the same kind where the price makes a
  higher high but RSI or MACD a lower high (bearish, דובית), or the price a lower low and
  the indicator a higher low (bullish, שורית); the second point must be recent.

How to read it:
- Overbought is not a sell signal and oversold is not a buy signal: a strong trend can
  stay overbought for a long time. "קניית יתר" is a state, not a prediction.
- MACD above its signal line means momentum is improving; above 0, the short average is
  above the long one.
- A divergence says momentum is weakening against the price. It is known only after the
  second turning point, so it is always late, and it does not time a reversal.

Hebrew terms: מומנטום, קניית יתר, מכירת יתר, סטייה שורית, סטייה דובית, קו האות.
