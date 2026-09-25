# Chart and candle patterns (facts pat_N.*)

Source: the site's own detectors, built from the pattern definitions in Thomas Bulkowski's
books (https://thepatternsite.com/); the rules are in our own words in the repository.

What the engine gives:
- pat_N.name (the Hebrew name), pat_N.status (בבנייה = forming, פריצה = breakout,
  פריצה כושלת = busted, אות נר = a candle signal), pat_N.direction (שורי / דובי / not
  known yet).
- pat_N.breakout: the level whose close confirms the pattern; pat_N.breakout_day if it did.
- pat_N.target: the book's measure rule (the pattern's height added at the breakout).
  It is a rule of thumb, not a forecast: write "יעד לפי גובה התבנית (לא תחזית)".
- pat_N.state, pat_N.sessions_since_breakout, pat_N.close_vs_breakout_pct: where the price
  is today against the breakout. A price back inside the pattern makes the breakout
  doubtful: say so, never present that breakout as live.
- pat_N.invalidation: the price whose close would cancel the pattern ("ביטול התבנית").
- pat_N.line_now: the broken line carried to today (a wedge's or triangle's line keeps its
  slope); state and close_vs_breakout_pct are measured against it.
- pat_N.state can say: beyond the line; back to test it ("חזר לבדוק את קו הפריצה/השבירה":
  a return to the broken line after moving away is common and is not a failure while the
  price stays on its side); back inside (doubtful); or failed (past the cancel level).
- pat_N.target_reached_day: the measured target was already reached: say so, never give
  it as a level ahead.

How to read it:
- A forming pattern is not confirmed until a close beyond its breakout level.
- A busted or failed pattern failed after breaking out; say that it failed. A failed
  bullish pattern leans negative (red light), a failed bearish one positive (green).
- A pattern still forming has no target yet: give only the line whose close would break it.
- On the breakout day itself (sessions_since_breakout 0) the price "סגר היום לראשונה
  מעל קו הפריצה": never "נשאר מעליו".
- Plain words on first use: "ראש וכתפיים (שלוש פסגות, האמצעית הגבוהה)", "תחתית כפולה
  (שני שפלים באותו אזור)", "ספל וידית (שקע מעוגל ואחריו ירידה קטנה)", "משולש/טריז
  (שני קווים מתכנסים)"; a pattern's direction as "(תבנית עלייה)" / "(תבנית ירידה)".
- The price breaks out of a pattern ("המחיר פרץ מתבנית ..." / "נשבר מתבנית ...");
  a pattern does not break out by itself. Never add a pattern's textbook bias.
- Candle signals are short-term and weak on their own.
- Do not quote success rates or statistics: the facts do not contain them.
