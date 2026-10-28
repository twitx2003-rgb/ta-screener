---
name: earnings-visual-picker
team: news-desk
does: for a large company's quarterly results, picks the X picture that breaks the reported numbers down and reads the stock's move after the report from the posts
used_by: tascreen/xnews.py earnings_visuals (one call per earnings story of a company above xnews.earnings_min_cap_usd)
---
A very large US company has just reported its quarterly results, and the investor's news
channel is sending the story. You get recent X posts about the company's ticker, each with
its post_id, author and text; some carry a picture, and the pictures come in the order of
the `image` numbers in the list.

1. `breakdown_post_id`: the post whose picture best shows how THIS quarter's reported
   numbers break down. Best: an income statement drawn as a flow (revenue by segment ->
   gross profit -> operating profit -> net income), or revenue by segment or region with
   the quarter's figures. Also good: a table of the quarter's results against the
   estimates (revenue, EPS, guidance). Not good: a price chart alone, a meme, a photo of
   the CEO or a product, a picture of an earlier quarter, or a different company. If no
   picture qualifies, return "".
   `breakdown_he`: ONE short Hebrew sentence (at most about 20 words) saying what the
   picture shows ("פירוק ההכנסות של הרבעון לפי מגזרים, מההכנסות ועד הרווח הנקי"). Only
   what is visible. "" when there is no picture.

2. The stock's move after the report, ONLY when a post's text states it as a number:
   `move_pct` (signed, as written: +6.2 or -3.1), `move_session` ("after_hours",
   "premarket" or "regular", from the post's words: "after hours", "AH", "pre-market",
   "shares up X% today"), and `move_post_id` (the post that states it). Copy the number;
   never compute it from prices, never take a move of a different stock or day. When no
   post states it, `move_pct` is null and the other two are "".

Hebrew style: the same as the news desk's (a verb in the sentence, Hebrew words for
financial terms, company names in Hebrew letters when they have a common Hebrew form,
the ticker only in parentheses). No advice and no forecasts.
