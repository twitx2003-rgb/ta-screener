# Chart and candle patterns (facts pat_N.*)

Source: the site's own detectors, built from the pattern definitions in Thomas Bulkowski's
books (https://thepatternsite.com/); the rules are in our own words in the repository.

What the engine gives:
- pat_N.name (the Hebrew name), pat_N.status (בבנייה = forming, פריצה = breakout,
  פריצה כושלת = busted, אות נר = a candle signal), pat_N.direction (שורי / דובי / not
  known yet).
- pat_N.breakout: the level whose close confirms the pattern; pat_N.breakout_day if it did.
- pat_N.target: the book's measure rule (the pattern's height added at the breakout).
  It is a rule of thumb, not a forecast: always write "יעד לפי כלל המדידה".
- pat_N.invalidation: the price whose close would cancel the pattern.

How to read it:
- A forming pattern is not confirmed until a close beyond its breakout level.
- A busted pattern failed after breaking out; say that it failed, nothing more.
- Candle signals are short-term and weak on their own.
- Do not quote success rates or statistics: the facts do not contain them.
