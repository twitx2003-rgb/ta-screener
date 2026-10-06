"""Breaking Wall Street news from X accounts the owner chose, to the Telegram bot.

Every run (xnews.yml, every 10 minutes) reads the chosen accounts' new posts through
twitterapi.io (a paid third-party reader: 1 USD = 100,000 credits, 15 credits a call at
least, 0.15 USD per 1,000 posts; checked 2026-09-27), asks Claude once which of them
matter for Wall Street, and sends only those as a short Hebrew summary with a link.
Nothing new -> no model call; nothing important -> no message.

The API shape below is from the service's documentation (GET
/twitter/tweet/advanced_search, header X-API-Key, query + queryType + cursor; posts
under "tweets" with id, text, url, createdAt, author, isReply; X search operators
since_time: and -filter:replies). It is NOT yet confirmed
against a real answer: `run.py --x-discover ACCOUNT` prints the real keys, and every
field goes through `pick`, which fails with the keys actually present.

The key is a secret: X_API_KEY (a GitHub secret on Actions) or ~/.ta-screener/x.json on
the owner's computer; never printed. Post texts never go to the public Actions log.
"""
from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Callable

from .errors import ProviderError
from .agents import prompt as agent_prompt
from .analyst.text_rules import banned, style_warnings
from . import watchlist
from .fields import pick
from .llm import UsageLimit

API = "https://api.twitterapi.io"
CREDENTIALS = Path("~/.ta-screener/x.json")
ACCOUNT = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")
# Accounts per search call ("from:a OR from:b ..."): fewer calls, same posts. Every call costs
# 15 credits even when it finds nothing, and about half the bill was such empty calls (owner,
# 2026-10-06: 10 accounts a call -> 7 calls a pass), so the cleaned list fits one call.
QUERY_ACCOUNTS = 20
# Pages of up to 20 posts per call: 10 posts per account at most, as with 10 accounts and 5
# pages, so the morning's catch-up of the night is not cut shorter by the bigger groups.
MAX_PAGES = 10
OVERLAP_S = 120              # each search starts a little before the last one ended; ids dedupe
# After a gap (the night, a weekend, a stopped workflow) at most this much is read back. It
# was one hour (a night of 63 accounts flooded the chat); since `ration` sends at most two a
# round, it covers the night (03:00-07:00 Israel), so the morning digest (tascreen/digest.py)
# has the US evening's news too (owner, 2026-09-29).
MAX_LOOKBACK_S = int(4.5 * 3600)
SENT_KEPT = 2000             # post ids remembered so a post is never judged or sent twice
SUMMARY_MAX = 220            # the owner wants it short (2026-09-27): one sentence
ANALYSIS_MAX = 360           # ...and then a short analysis (owner, same day): 1-2 sentences
PHOTO_HOST = "https://pbs.twimg.com/"
# The reader's balance (owner, 2026-09-28): checked once a week, a Telegram warning when low.
# twitterapi.io's low-balance e-mail: 1,000,000 credits = 10.00 USD; its price: 0.15 USD per
# 1,000 posts. GET /oapi/my/info answered recharge_credits and total_bonus_credits (live, 2026-09-28).
CREDITS_PER_USD = 100_000
USD_PER_POST = 0.15 / 1000
BALANCE_EVERY_S = 7 * 24 * 3600
TOP_UP_URL = "https://twitterapi.io/dashboard"


def account_name(raw: str) -> str:
    match = ACCOUNT.match(str(raw).strip())
    if not match:
        raise ProviderError(f"'{raw}' is not an X account name (letters, digits, _; up to 15)")
    return match.group(1)


@dataclass(frozen=True)
class Post:
    id: str
    author: str
    text: str
    url: str
    created_at: str
    is_reply: bool
    photo: str = ""              # the first attached photo's address, if any


class XSource:
    def __init__(self, key: str, *, get: Callable[[str, dict], dict] | None = None):
        self.key = key.strip()
        self._get = get or self._http
        self.calls = 0

    def _redact(self, text: str) -> str:
        return str(text).replace(self.key, "<key>") if self.key else str(text)

    def _http(self, path: str, params: dict) -> dict:
        request = urllib.request.Request(f"{API}{path}?{urllib.parse.urlencode(params)}",
                                         headers={"X-API-Key": self.key})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderError(self._redact(f"X reader {path}: HTTP {exc.code}")) from None
        except (OSError, ValueError) as exc:
            raise ProviderError(self._redact(f"X reader {path}: {type(exc).__name__}: {exc}")) from None

    def balance_usd(self) -> float:
        """The account's credits (paid and bonus) in USD."""
        info = self._get("/oapi/my/info", {})
        if not isinstance(info, dict):
            raise ProviderError(f"X reader balance: expected an object, got {type(info).__name__}")
        credits = (float(pick(info, ["recharge_credits"], context="X reader balance"))
                   + float(pick(info, ["total_bonus_credits"], context="X reader balance")))
        return credits / CREDITS_PER_USD

    def search_page(self, query: str, cursor: str = "") -> dict:
        self.calls += 1
        params = {"query": query, "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
        answer = self._get("/twitter/tweet/advanced_search", params)
        if not isinstance(answer, dict):
            raise ProviderError(f"X reader: expected an object, got {type(answer).__name__}")
        if answer.get("status") == "error":
            raise ProviderError(self._redact(f"X reader: {answer.get('msg') or answer.get('message') or 'error'}"))
        return answer

    def new_posts(self, accounts: list[str], since: int, *, with_replies: bool = False) -> list[Post]:
        """Posts by these accounts since the Unix time `since`, oldest first."""
        found: dict[str, Post] = {}
        for start in range(0, len(accounts), QUERY_ACCOUNTS):
            group = accounts[start:start + QUERY_ACCOUNTS]
            query = "(" + " OR ".join(f"from:{a}" for a in group) + f") since_time:{int(since)}"
            if not with_replies:
                query += " -filter:replies"      # replies are paid for too; isReply stays checked
            cursor = ""
            for _ in range(MAX_PAGES):
                page = self.search_page(query, cursor)
                rows = pick(page, ("tweets",), context="X search") or []
                for row in rows:
                    post = to_post(row)
                    if with_replies or not post.is_reply:
                        found[post.id] = post
                # the service can offer a next page that is empty (live, 2026-09-27): each is paid
                if not rows or not page.get("has_next_page") or not page.get("next_cursor"):
                    break
                cursor = str(page["next_cursor"])
        return sorted(found.values(), key=lambda p: (len(p.id), p.id))


def to_post(row: dict) -> Post:
    context = "X post"
    author = pick(row, ("author",), context=context)
    name = pick(author, ("userName", "username", "screen_name"), context="X post author")
    post_id = str(pick(row, ("id",), context=context))
    url = pick(row, ("url", "twitterUrl"), context=context, allow_null=True) or \
        f"https://x.com/{name}/status/{post_id}"
    return Post(id=post_id, author=str(name), text=str(pick(row, ("text",), context=context)),
                url=str(url), created_at=str(pick(row, ("createdAt",), context=context, allow_null=True) or ""),
                is_reply=bool(pick(row, ("isReply",), context=context, allow_null=True)), photo=first_photo(row))


def first_photo(row: dict) -> str:
    """The first photo attached to a post, or "". Checked live (2026-09-27): media sit in
    extendedEntities.media[], each with `type` ("photo", "video", "animated_gif") and
    `media_url_https` on pbs.twimg.com. A post without media has no such list, which is
    not an error; a video's still frame is not taken."""
    media = (row.get("extendedEntities") or {}).get("media") if isinstance(row.get("extendedEntities"), dict) else None
    for item in media if isinstance(media, list) else []:
        if isinstance(item, dict) and item.get("type") == "photo":
            url = str(item.get("media_url_https") or "")
            if url.startswith(PHOTO_HOST):
                return url
    return ""


# --- the owner's key -------------------------------------------------------------------

def key_from_environment() -> str | None:
    if os.environ.get("X_API_KEY"):
        return os.environ["X_API_KEY"].strip()
    try:
        return json.loads(CREDENTIALS.expanduser().read_text(encoding="utf-8"))["key"].strip()
    except (OSError, ValueError, KeyError, AttributeError):
        return None


def save_key(key: str) -> Path:
    path = CREDENTIALS.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": key.strip()}), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


# --- what the last runs saw --------------------------------------------------------------

def load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# --- Claude picks what matters ------------------------------------------------------------

# tascreen/agents/news-screener.md, with news-deduper.md: one call does both jobs
SYSTEM = agent_prompt("news-screener", "news-deduper")
# Stories sent this recently are shown to the deduper. It was two hours, and the channel got
# the same story twice 2-3 hours apart from two accounts, with different figures (2026-09-29).
# Twelve hours covers a whole US session plus its evening, so a story sent at the open is
# still known when a second account repeats it late at night.
SENT_WINDOW_S = 12 * 3600
SENT_SHOWN_MAX = 30          # the newest this many go into the prompt (about 30 x 250 chars)
SENT_RECENT_KEPT = 60        # rows kept in the state file (the explainer reads them too)
RECENT_WINDOW_S = 3 * 3600   # posts kept this long for the market explainer (tascreen/explain.py)
RECENT_MAX = 300
SOURCES_SHOWN = 3
# Every pick for the morning digest (owner, 2026-09-29): sent, held back by `ration`, and
# the updates rated 2 that are never sent ("interesting, not necessarily sent"), from a
# session's open to 10:00 the next day, and over a weekend from Friday's open to Monday
DAY_LOG_S = 4 * 24 * 3600
DAY_LOG_MAX = 1500
DIGEST_FROM = 2
# The owner wants few, hand-picked stories (2026-09-28: 29 went out in 90 minutes): a round
# sends at most `max_per_round`, the most important first, and the regular ones (below 5)
# share a budget that refills through the news hours, so the evening is not left empty.
# A story rated 5 (market-moving now) is never held back by the budget.
NEWS_HOURS = 20              # 07:00-03:00 Israel time (.github/xnews.sh)
URGENT = 5


TICKER = re.compile(r"(?<![A-Za-z])[A-Z]{1,5}(?![A-Za-z])")
NOT_COMPANIES = {"AI", "CPI", "PCE", "GDP", "ISM", "PMI", "ADP", "ETF", "IPO", "SEC", "FDA", "FOMC", "ECB",
                 "BOJ", "US", "USA", "UK", "EU", "IMF", "OPEC", "LNG", "S", "P", "Q", "CEO", "CFO", "EPS"}


def names_company(pick_: dict) -> bool:
    """The story names a company by its ticker (owner, 2026-10-01: more company updates):
    a capitalised 1-5 letter word that is not an economic or agency abbreviation."""
    return any(t not in NOT_COMPANIES for t in TICKER.findall(str(pick_.get("summary_he", ""))))


def ration(picks: list[tuple["Post", dict]], budget: dict, stamp: int, day: str, *,
           max_per_round: int, daily_max: int) -> tuple[list[tuple["Post", dict]], dict, int]:
    """(the picks to send, most important first; the budget after them; how many were held
    back). The budget is a token per regular story, refilled at `daily_max` per NEWS_HOURS
    up to `max_per_round`, and at most `daily_max` regular stories a day. A story rated 5
    needs no token but uses one if there is one; more sources rank a story higher."""
    rate = daily_max / (NEWS_HOURS * 3600)
    tokens = float(budget.get("tokens", max_per_round))
    tokens = min(float(max_per_round), tokens + max(0, stamp - int(budget.get("at", stamp))) * rate)
    sent_today = int(budget.get("sent", 0)) if budget.get("day") == day else 0
    order = sorted(range(len(picks)), key=lambda i: (-picks[i][1]["importance"], not names_company(picks[i][1]),
                                                     -len(picks[i][1]["sources"]), i))
    ranked = [picks[i] for i in order]
    urgent = [pk for pk in ranked if pk[1]["importance"] >= URGENT][:max_per_round]
    tokens = max(0.0, tokens - len(urgent))
    room = max(0, min(max_per_round - len(urgent), int(tokens), daily_max - sent_today))
    regular = [pk for pk in ranked if pk[1]["importance"] < URGENT][:room]
    tokens -= len(regular)
    after = {"tokens": round(tokens, 4), "at": stamp, "day": day, "sent": sent_today + len(regular)}
    return urgent + regular, after, len(picks) - len(urgent) - len(regular)


def schema() -> dict:
    return {
        "type": "object",
        "properties": {"picks": {"type": "array", "items": {
            "type": "object",
            "properties": {"post_id": {"type": "string"},
                           "importance": {"type": "integer", "minimum": 1, "maximum": 5},
                           "summary_he": {"type": "string"},
                           "analysis_he": {"type": "string"},
                           "same_story_as": {"type": "string"},
                           "repeat_of_sent": {"type": "boolean"}},
            "required": ["post_id", "importance", "summary_he", "analysis_he", "same_story_as",
                         "repeat_of_sent"],
            "additionalProperties": False}}},
        "required": ["picks"],
        "additionalProperties": False,
    }


def user_prompt(posts: list[Post], already_sent: list[dict] | None = None,
                holdings: list[str] | None = None) -> str:
    data: dict[str, Any] = {"already_sent": already_sent or [],
                            "posts": [{"post_id": p.id, "author": p.author, "time": p.created_at, "text": p.text}
                                      for p in posts]}
    if holdings:
        data = {"investor_holdings": list(holdings), **data}
    return json.dumps(data, ensure_ascii=False, indent=1)


WEEKEND_NOTE = ("It is the weekend and US markets are closed: rate 4 or 5 only what is dramatic, "
                "or what the investor needs to know before the coming trading week (events, data "
                "and earnings due, big news from the weekend).")


def is_weekend(now: datetime) -> bool:
    """Saturday or Sunday in New York (the owner, 2026-09-28: on weekends only the dramatic
    and what matters for the coming week)."""
    return now.astimezone(ZoneInfo("America/New_York")).weekday() >= 5


def triage(posts: list[Post], llm, min_importance: int, note: str = "", *,
           already_sent: list[dict] | None = None, holdings: list[str] | None = None,
           counts: dict[str, int] | None = None) -> tuple[list[tuple[Post, dict]], int]:
    """(the picks at or above `min_importance`, in post order; how many answers were rejected).
    A post marked as the same story as another is folded into it (its account becomes a
    source of the main one); a repeat of a story sent within SENT_WINDOW_S is dropped.
    `counts` gets "merged", "repeats" and "style_slips" (Hebrew style warnings)."""
    if not posts:
        return [], 0
    user = (note + "\n\n" if note else "") + user_prompt(posts, already_sent, holdings)
    answer, _ = llm.complete(system=SYSTEM, user=user, schema=schema())
    by_id = {p.id: p for p in posts}
    picks: dict[str, dict] = {}
    rejected = 0
    same: dict[str, str] = {}
    repeats: set[str] = set()
    for item in answer.get("picks") or []:
        if not isinstance(item, dict):
            rejected += 1
            continue
        post_id, summary = str(item.get("post_id", "")), str(item.get("summary_he", "")).strip()
        try:
            importance = int(item.get("importance", 0))
        except (TypeError, ValueError):
            importance = 0
        if post_id not in by_id or not summary or not 1 <= importance <= 5:
            rejected += 1                  # an invented post, or an empty or broken answer
            continue
        main = str(item.get("same_story_as") or "")
        if main and main != post_id and main in by_id:
            same[post_id] = main
            continue
        if item.get("repeat_of_sent") is True:
            repeats.add(post_id)
            continue
        if importance >= min_importance:
            analysis = str(item.get("analysis_he", "")).strip()
            if banned(analysis):             # advice or forecast wording: the news goes without it
                analysis = ""
            picks[post_id] = {"importance": importance, "summary_he": summary[:SUMMARY_MAX],
                              "analysis_he": analysis[:ANALYSIS_MAX], "sources": []}
            if counts is not None:           # style slips are counted, never a reason to drop
                slips = len(style_warnings(f"{summary} {analysis}"))
                counts["style_slips"] = counts.get("style_slips", 0) + slips
    merged = 0
    for dup, main in same.items():          # a chain (a -> b -> c) ends at its last main post
        seen = {dup}
        while main in same and main not in seen:
            seen.add(main)
            main = same[main]
        if main in picks:
            picks[main]["sources"].append((by_id[dup].author, by_id[dup].url))
            merged += 1
    if counts is not None:
        counts["merged"] = counts.get("merged", 0) + merged
        counts["repeats"] = counts.get("repeats", 0) + len(repeats)
    return [(p, picks[p.id]) for p in posts if p.id in picks], rejected


def item(post: Post, pick_: dict, *, with_image: bool = False) -> str:
    """One pick in Telegram HTML: mark, account, the sentence and a link; then the short
    analysis, and under a photo the line that says what the picture shows."""
    mark = {5: "🔴", 4: "🟠"}.get(pick_["importance"], "🔵")
    sources = [(post.author, post.url)]
    for author, url in pick_.get("sources") or []:
        if author not in {a for a, _ in sources}:
            sources.append((author, url))
    names = " · ".join(f"<b>{html.escape(a)}</b>" for a, _ in sources[:SOURCES_SHOWN])
    if len(sources) > SOURCES_SHOWN:
        names += f" +{len(sources) - SOURCES_SHOWN}"
    links = " ".join(f'<a href="{html.escape(u, quote=True)}">↗</a>' for _, u in sources[:SOURCES_SHOWN])
    text = f"{mark} {names}: {html.escape(pick_['summary_he'])} {links}"
    if pick_.get("analysis_he"):
        text += f"\n💡 {html.escape(pick_['analysis_he'])}"
    if with_image and pick_.get("image_he"):
        text += f"\n📊 {html.escape(pick_['image_he'])}"
    return text


# --- what a picture shows ------------------------------------------------------------------

IMAGE_SYSTEM = agent_prompt("chart-reader")   # tascreen/agents/chart-reader.md

IMAGES_MAX = 4               # pictures per model call
IMAGE_BYTES_MAX = 5 * 1024 * 1024
IMAGE_MAX = 260
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")


def image_schema() -> dict:
    return {
        "type": "object",
        "properties": {"images": {"type": "array", "items": {
            "type": "object",
            "properties": {"post_id": {"type": "string"}, "image_he": {"type": "string"}},
            "required": ["post_id", "image_he"],
            "additionalProperties": False}}},
        "required": ["images"],
        "additionalProperties": False,
    }


def fetch_image(url: str) -> tuple[str, bytes] | None:
    """(media type, bytes) of a picture on pbs.twimg.com, or None."""
    if not url.startswith(PHOTO_HOST):
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ta-screener"}),
                                    timeout=20) as response:
            media = response.headers.get_content_type()
            data = response.read(IMAGE_BYTES_MAX + 1)
    except (OSError, ValueError):
        return None
    return (media, data) if media in IMAGE_TYPES and 0 < len(data) <= IMAGE_BYTES_MAX else None


def explain_images(picks: list[tuple[Post, dict]], llm,
                   fetch: Callable[[str], tuple[str, bytes] | None] = fetch_image) -> int:
    """Adds `image_he` to the picks whose photo is a chart or table (one model call for up
    to IMAGES_MAX pictures). Returns how many got one."""
    chosen, images = [], []
    for post, pick_ in picks:
        if post.photo and len(chosen) < IMAGES_MAX:
            got = fetch(post.photo)
            if got is not None:
                chosen.append((post, pick_))
                images.append(got)
    if not chosen:
        return 0
    listing = json.dumps([{"image": n + 1, "post_id": p.id, "author": p.author, "text": p.text}
                          for n, (p, _) in enumerate(chosen)], ensure_ascii=False, indent=1)
    answer, _ = llm.complete_with_images(system=IMAGE_SYSTEM, user=listing, images=images,
                                         schema=image_schema())
    by_id = {p.id: k for p, k in chosen}
    done = 0
    for entry in answer.get("images") or []:
        if not isinstance(entry, dict):
            continue
        pick_ = by_id.get(str(entry.get("post_id", "")))
        text = str(entry.get("image_he", "")).strip()
        if pick_ is not None and text:
            pick_["image_he"] = text[:IMAGE_MAX]
            done += 1
    return done


def message(picks: list[tuple[Post, dict]]) -> str:
    """One Telegram HTML message for these picks (Telegram allows 4096 characters)."""
    parts = [item(post, pick_) for post, pick_ in picks]
    # whole items only: a cut inside a tag makes Telegram refuse the HTML
    for shown in range(len(parts), 0, -1):
        rest = len(parts) - shown
        text = "\n\n".join(parts[:shown]) + (f"\n\n(ועוד {rest})" if rest else "")
        if len(text) <= 4000:
            return text
    return parts[0][:3900]


def deliver(picks: list[tuple[Post, dict]], send: Callable[[str], None],
            send_photo: Callable[[str, str], None] | None) -> int:
    """A round's picks in one message (owner, 2026-10-01: "two stories in a round go in one
    message, not two"): with two or more, every pick is text, a photo's place taken by the
    line that says what it shows (the link opens the post with it). A lone pick with a photo
    goes as the photo, its line as the caption (under Telegram's 1024); a photo Telegram
    cannot fetch goes as text instead. Returns how many went with a photo."""
    if len(picks) > 1:
        text = "\n\n".join(item(post, pick_, with_image=True) for post, pick_ in picks)
        send(text if len(text) <= 4000 else message(picks))
        return 0
    with_photo = [(p, k) for p, k in picks
                  if p.photo and send_photo is not None and len(item(p, k, with_image=True)) <= 1024]
    plain = [(p, k) for p, k in picks if (p, k) not in with_photo]
    if plain:
        send(message(plain))
    photos = 0
    for post, pick_ in with_photo:
        try:
            send_photo(post.photo, item(post, pick_, with_image=True))
            photos += 1
        except ProviderError:
            send(message([(post, pick_)]))
    return photos


# --- one run ------------------------------------------------------------------------------

PENDING_MAX = 300            # posts waiting for Claude, at most (the newest are kept)
PENDING_MAX_AGE_S = 3 * 3600  # older than this is no longer breaking news: dropped unjudged
LIMIT_PAUSE_S = 3600         # after the subscription's usage limit, no model call for an hour


def _post_row(post: Post, added: int) -> dict:
    return {"id": post.id, "author": post.author, "text": post.text, "url": post.url,
            "created_at": post.created_at, "photo": post.photo, "added": added}


def _row_post(row: dict) -> Post:
    return Post(id=str(row["id"]), author=str(row["author"]), text=str(row["text"]), url=str(row["url"]),
                created_at=str(row.get("created_at", "")), is_reply=False, photo=str(row.get("photo", "")))


def low_balance_message(usd: float, daily_read_cap: int) -> str:
    days = int(usd / (daily_read_cap * USD_PER_POST))
    return (f"💳 <b>היתרה לקריאת הציוצים נמוכה: נשארו {usd:.2f} דולר.</b>\n"
            f"גם בקצב הכי גבוה ({daily_read_cap:,} ציוצים ביום) זה מספיק לעוד {days} ימים לפחות. "
            f"כשהיתרה תיגמר, החדשות יפסיקו להגיע.\n"
            f'<a href="{TOP_UP_URL}">להטענה</a>')


def check_balance(source: XSource, balance: dict, stamp: int, send: Callable[[str], None], *,
                  low_usd: float, daily_read_cap: int) -> tuple[dict, bool | None]:
    """Once every BALANCE_EVERY_S: the reader's balance, and a warning below `low_usd`.
    (the state's "balance" entry after it; True/False = low or not, None = not checked).
    A failed check is tried again next run; the amount never reaches the public log."""
    if low_usd <= 0 or stamp - int(balance.get("checked", 0)) < BALANCE_EVERY_S:
        return balance, None
    try:
        usd = source.balance_usd()
    except ProviderError:
        return balance, None
    if usd < low_usd:
        send(low_balance_message(usd, daily_read_cap))
    return {"checked": stamp}, usd < low_usd


def run_once(*, accounts: list[str], source: XSource, llm_factory: Callable[[], Any], send: Callable[[str], None],
             state_path: Path, now: datetime, min_importance: int, daily_read_cap: int,
             weekend_min_importance: int = 4,
             llm_daily_cap: int = 45, llm_min_interval_s: int = 1200,
             max_per_round: int = 2, daily_max: int = 12, low_balance_usd: float = 0.0,
             holdings: list[str] | tuple = (), send_private: Callable[[str], None] | None = None,
             send_photo: Callable[[str, str], None] | None = None,
             fetch: Callable[[str], tuple[str, bytes] | None] = fetch_image,
             first_lookback_s: int = 900, with_replies: bool = False) -> dict[str, Any]:
    """Read, pick, send, remember. Returns counts only (safe for the public log).

    Posts are read every run, but Claude is asked at most every `llm_min_interval_s`
    and `llm_daily_cap` times a day (the subscription is shared with the chart
    analyses); the posts in between wait in the state's "pending" list and are
    judged together. Of the picks, only the best few go out (`ration`). The state is saved
    only after the message went out, so a failed send is retried by the next run and a sent
    post is never sent again."""
    state = load_state(state_path)
    stamp = int(now.timestamp())
    day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    reads = state.get("reads") if isinstance(state.get("reads"), dict) else {}
    if reads.get("day") != day:
        reads = {"day": day, "posts": 0}
    model = state.get("llm") if isinstance(state.get("llm"), dict) else {}
    if model.get("day") != day:
        model = {"day": day, "calls": 0, "last": model.get("last", 0), "paused_until": model.get("paused_until", 0)}
    budget = state.get("budget") if isinstance(state.get("budget"), dict) else {}
    balance = state.get("balance") if isinstance(state.get("balance"), dict) else {}
    summary: dict[str, Any] = {"accounts": len(accounts), "read": 0, "new": 0, "waiting": 0,
                               "judged": 0, "sent": 0, "rejected": 0, "calls": 0,
                               "claude_today": model["calls"]}
    if not accounts:
        summary["status"] = "no accounts"
        return summary
    balance, low = check_balance(source, balance, stamp, send, low_usd=low_balance_usd,
                                 daily_read_cap=daily_read_cap)
    if low is not None:
        summary["balance_low"] = low
    seen = list(state.get("seen") or [])
    pending = [r for r in state.get("pending") or []
               if isinstance(r, dict) and stamp - int(r.get("added", 0)) <= PENDING_MAX_AGE_S]
    sent_recent = [r for r in state.get("sent_recent") or []
                   if isinstance(r, dict) and stamp - int(r.get("at", 0)) <= SENT_WINDOW_S]
    recent = [r for r in state.get("recent") or []
              if isinstance(r, dict) and stamp - int(r.get("added", 0)) <= RECENT_WINDOW_S]
    day_log = [r for r in state.get("day_log") or []
               if isinstance(r, dict) and stamp - int(r.get("at", 0)) <= DAY_LOG_S]
    since = max(int(state.get("since") or stamp - first_lookback_s), stamp - MAX_LOOKBACK_S)
    if reads["posts"] < daily_read_cap:
        posts = source.new_posts(accounts, since - OVERLAP_S, with_replies=with_replies)
        summary["calls"], summary["read"] = source.calls, len(posts)
        seen_set = set(seen)
        new = [p for p in posts if p.id not in seen_set]
        summary["new"] = len(new)
        seen = (seen + [p.id for p in new])[-SENT_KEPT:]
        pending = (pending + [_post_row(p, stamp) for p in new])[-PENDING_MAX:]
        recent = (recent + [_post_row(p, stamp) for p in new])[-RECENT_MAX:]
        reads["posts"] += len(posts)
        since = stamp
    else:
        summary["read_status"] = "daily cap"
    status = "ok"
    mine: list[tuple[Post, dict]] = []
    due = (pending and model["calls"] < llm_daily_cap and stamp >= int(model.get("paused_until", 0))
           and stamp - int(model.get("last", 0)) >= llm_min_interval_s)
    if due:
        try:
            llm = llm_factory()
            weekend = is_weekend(now)
            counts: dict[str, int] = {}
            already = [{"author": r["author"], "summary_he": r["summary_he"],
                        "minutes_ago": (stamp - int(r["at"])) // 60} for r in sent_recent[-SENT_SHOWN_MAX:]]
            threshold = max(min_importance, weekend_min_importance) if weekend else min_importance
            picks, summary["rejected"] = triage(
                [_row_post(r) for r in pending], llm, min(DIGEST_FROM, threshold),
                WEEKEND_NOTE if weekend else "", already_sent=already, holdings=list(holdings), counts=counts)
            summary.update(counts)
        except UsageLimit:
            model["paused_until"] = stamp + LIMIT_PAUSE_S
            picks, status = [], "claude limit"
        else:
            model["calls"] += 1
            model["last"] = stamp
            summary["judged"] = len(pending)
            pending = []
            every = picks                    # all for the digest; only the important may be sent
            picks = [pk for pk in every if pk[1]["importance"] >= threshold]
            picks, budget, summary["held_back"] = ration(picks, budget, stamp, day, max_per_round=max_per_round,
                                                         daily_max=daily_max)
            chosen = {p.id for p, _ in picks}
            # news about the owner's watchlist that the group did not get: to the private chat
            mine = [(p, k) for p, k in every if k["importance"] >= 3 and p.id not in chosen
                    and holdings and watchlist.mentions(f"{p.text} {k['summary_he']}", list(holdings))]
            day_log = (day_log + [{"at": stamp, "id": p.id, "author": p.author, "url": p.url, "time": p.created_at,
                                   "importance": k["importance"], "summary_he": k["summary_he"],
                                   "analysis_he": k["analysis_he"], "sources": [a for a, _ in k["sources"]],
                                   "sent": p.id in chosen} for p, k in every])[-DAY_LOG_MAX:]
        if send_photo is not None and any(p.photo for p, _ in picks):
            try:                             # a second look, at the pictures: never blocks the news
                summary["explained"] = explain_images(picks, llm, fetch)
                model["calls"] += 1
            except UsageLimit:
                model["paused_until"] = stamp + LIMIT_PAUSE_S
            except ProviderError as exc:
                summary["explain_error"] = type(exc).__name__
        if mine and send_private is not None:
            send_private(watchlist.NEWS_HEAD + "\n\n" + message(mine))
            summary["holding_news"] = len(mine)
        if picks:
            summary["photos"] = deliver(picks, send, send_photo)
            sent_recent += [{"at": stamp, "author": p.author, "summary_he": k["summary_he"]}
                            for p, k in picks]
        summary["sent"] = len(picks)
    elif pending and model["calls"] >= llm_daily_cap:
        status = "claude daily cap"
    summary["waiting"] = len(pending)
    summary["claude_today"] = model["calls"]
    save_state(state_path, {"since": since, "seen": seen, "pending": pending, "reads": reads, "llm": model,
                            "budget": budget, "balance": balance, "sent_recent": sent_recent[-SENT_RECENT_KEPT:],
                            "recent": recent, "day_log": day_log})
    summary["status"] = status
    return summary


ENDED_TEXT = ("📰 תקופת החדשות מ-X הסתיימה (שלושה חודשים). לא ייקראו יותר ציוצים ולא ישולם עליהם. "
              "כדי להמשיך: לשנות את התאריך xnews.until בקובץ ההגדרות.")


def past_end(*, until: str, now: datetime, state_path: Path, send: Callable[[str], None]) -> bool:
    """After the `until` day (New York's date is not needed: a day late is fine) nothing
    is read; the owner is told once."""
    if now.astimezone(timezone.utc).date().isoformat() <= until:
        return False
    state = load_state(state_path)
    if not state.get("ended"):
        send(ENDED_TEXT)
        save_state(state_path, {**state, "ended": True, "pending": []})
    return True
