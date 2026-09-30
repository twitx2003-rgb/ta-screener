---
name: news-deduper
team: news-desk
does: marks posts that tell the same story as another post in the batch, or as news already sent in the last twelve hours, so the owner gets each story once
used_by: tascreen/xnews.py triage (the same call as news-screener: its instructions are appended)
---
Several accounts often report the same event within minutes (a data release, an earnings
number, a Fed remark, a company announcement). The investor wants each story once.

The input has two parts: `already_sent`, the stories the investor received in the last
twelve hours (account, Hebrew summary, minutes ago), and `posts`, the new posts. A story
sent hours ago is still sent: an account that reports it late is repeating it.

For every post you return in `picks`, also set:

- `same_story_as`: the post_id of another post in `posts` that reports the same event,
  or "" if none. Point every duplicate at ONE post: the clearest and most complete of
  the group, usually the first to report it. That post itself gets "". Rate and
  summarise that main post for the whole group; the duplicates may get any rating.
- `repeat_of_sent`: true when the post tells a story in `already_sent` again, with
  nothing new that matters. This includes another account's version of the same event
  with slightly different figures (a record "since 1998" against "since 1996"; odds
  "80% to 55%" against "78% to 41%"): that is the same story, told less precisely by
  someone, not news. It is a real update (false) only when something new happened after
  the sent story: the post says it is a revision, a correction or a later reading (a
  later close, the next data point, the odds after a new remark), or it adds a reaction or
  a reversal. A real update's `summary_he` starts with "עדכון:" and says what changed
  against what was sent (for example "עדכון: הסיכוי ירד שוב, ל-40%").
- The same applies inside `posts`: two accounts giving different figures for one event
  are one story (`same_story_as`); summarise the main post's figure, do not average or
  mix the two.

Same story means the same event, not the same topic: two different companies' earnings
are two stories; CPI reported by three accounts is one.
