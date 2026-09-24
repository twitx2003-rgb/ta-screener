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

How to read it:
- Traditional volume says when trading happened; the profile says at which prices.
- The POC and the value-area edges often act as support or resistance: many shares changed
  hands there. A price above the value area trades above where most of the volume was done.
- Rising volume on a move gives it more weight; a breakout on low volume is less convincing.
- Never state a volume number the facts do not give.

Hebrew terms: פרופיל נפח, מחיר השליטה (POC), אזור הערך, נפח ממוצע.
