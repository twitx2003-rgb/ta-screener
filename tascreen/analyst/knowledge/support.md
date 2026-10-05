# Support tests: the 20- and 150-day averages, and retests after a breakout (facts sma20.support.*, sma150.support.*, sma*.cross*, pat_N.retest*, zone_N.retest*)

Sources (ideas, in our own words): StockCharts ChartSchool, "Support and Resistance",
https://chartschool.stockcharts.com/table-of-contents/chart-analysis/support-and-resistance ;
"Moving Averages - Simple and Exponential" (averages as support in a trend),
https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential ;
Bulkowski, Encyclopedia of Chart Patterns, the introduction's notes on throwbacks (a price
that returns to the breakout line after the breakout). The thresholds are ours (rules.yaml).

Owner, 2026-10-05: support on the 150-day and the 20-day averages with volume that is not
weak, and breakouts with the support tests after them, matter in every analysis.

What the engine computes (analyst/support.py), each level as one value per session:
- A test: the price had closed well above the level (1.5 times the day's usual range,
  without touching it), then a session's low came back to within half a usual range of it.
  The test ends "held" when a close gets a full usual range above the level again, "broke"
  when a close falls half a usual range below it; still open at the last session it is a
  test now ("בודק עכשיו").
- sma20.support.* over about 3 months, sma150.support.* over about a year: .held and .broke
  count the tests; .streak the holds in a row; .strength in words ("תמיכה חזקה" needs two
  holds in a row, the last on volume that is not weak); .state only when a test is open or
  ended in the last sessions; .last_* the latest test (its low day, the bounce's day and
  volume, and the volume on the way down).
- sma20.cross / sma150.cross: the latest close across the average in about six weeks
  (a breakout above it or a break below it), its day and volume; after a breakout above,
  sma*.cross.retest says whether the price came back to the average and held.
- pat_N.retest (a bullish pattern's breakout line, followed with its slope) and
  zone_N.retest (a resistance zone the price broke out of): the same tests after the
  breakout, or "עוד לא חזר לבדוק" while the price is still away from the line.
- Volume: the bounce's busiest up-session against the 50 sessions before it. Under 0.85
  times the average it is weak, the same word the analysis uses for low volume.

How to read it:
- An average that held several times, with buyers showing up in the bounce (volume not
  weak), is a level the market has defended: the longer average (150) speaks of the long
  trend, the 20 of the short swing.
- A retest that held is a healthy sign for the breakout: the old resistance acted as
  support. A light pullback (low volume on the way down) with a bounce on volume that is
  not weak is the classic picture. A close back under the line weakens the breakout.
- A break of an average that had held is a warning, not a forecast.
- Quote the facts' own words; never invent a hold, a count or a volume the facts do not give.

Hebrew terms: בדיקת תמיכה, החזיק כתמיכה, נשבר, קפיצה מהממוצע, נפח לא חלש.
