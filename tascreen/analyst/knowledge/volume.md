# Volume and the volume profile (facts vp.*, volume.*)

Sources (ideas, in our own words): TradingView Help Center, "Volume profile indicators:
basic concepts",
https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/ ;
StockCharts ChartSchool, "Volume-by-Price",
https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/volume-by-price

What the engine computes:
- Volume by price over the analysed window (about a year of daily bars), 40 price rows;
  each day's volume is spread evenly over its high-low range. This approximates the real
  profile from daily bars, so it can differ from TradingView's intraday-based profile.
- vp.poc: the price row with the most volume (Point of Control).
- vp.val / vp.vah: the bottom and top of the value area, the range holding 70% of the volume.
- volume.ratio: average volume of the last 20 sessions divided by the last 50;
  volume.trend: rising / falling / steady.
- volume.last, pat_N.breakout_volume, zone_N.broken_volume, volume.spike: the same
  ratios in words with the number ("גבוה, פי 1.9 מהממוצע", "רגיל, סביב הממוצע", "נמוך, כ-22%
  מתחת לממוצע"): quote these, never a bare ratio. Normal volume (0.85 to 1.15) confirms
  nothing; a breakout without high volume is less convincing (a yellow light).
- volume.spike (with spike_day, spike_change_pct): the busiest session of the last five,
  from 1.8 times the average: an unusual day worth one clause.
- volume.last_ratio, pat_N.breakout_volume_ratio, zone_N.broken_volume_ratio: one
  session's volume divided by the average of the 50 sessions before it (1.5 = half as much
  again as usual).

How to read it:
- Traditional volume says when trading happened; the profile says at which prices.
- The POC and the value-area edges often act as support or resistance: many shares changed
  hands there. A price above the value area trades above where most of the volume was done.
- Rising volume on a move gives it more weight; a breakout on low volume is less convincing.
  With a fresh breakout, say its day's volume against the average ("בנפח גבוה פי 1.8 מהממוצע"
  from 1.5, "בנפח נמוך מהממוצע" under 1).
- The POC in plain words: "נפח מרבי: המחיר שבו נסחר הכי הרבה".
- Never state a volume number the facts do not give.

Hebrew terms: פרופיל נפח, מחיר השליטה (POC), אזור הערך, נפח ממוצע.
