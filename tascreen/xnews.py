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
from .analyst.text_rules import banned
from .fields import pick
from .llm import UsageLimit

API = "https://api.twitterapi.io"
CREDENTIALS = Path("~/.ta-screener/x.json")
ACCOUNT = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")
QUERY_ACCOUNTS = 10          # accounts per search call ("from:a OR from:b ..."); fewer calls, same posts
MAX_PAGES = 5                # pages of up to 20 posts per call; more than 100 new posts in 10 minutes is a flood
OVERLAP_S = 120              # each search starts a little before the last one ended; ids dedupe
# After a gap (the night, a weekend, a stopped workflow) only the last hour is read: older
# posts are no longer news, and a night of 63 accounts at once would flood the chat.
MAX_LOOKBACK_S = 3600
SENT_KEPT = 2000             # post ids remembered so a post is never judged or sent twice
SUMMARY_MAX = 220            # the owner wants it short (2026-09-27): one sentence
ANALYSIS_MAX = 360           # ...and then a short analysis (owner, same day): 1-2 sentences
PHOTO_HOST = "https://pbs.twimg.com/"


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

SYSTEM = agent_prompt("news-screener")     # tascreen/agents/news-screener.md


def schema() -> dict:
    return {
        "type": "object",
        "properties": {"picks": {"type": "array", "items": {
            "type": "object",
            "properties": {"post_id": {"type": "string"},
                           "importance": {"type": "integer", "minimum": 1, "maximum": 5},
                           "summary_he": {"type": "string"},
                           "analysis_he": {"type": "string"}},
            "required": ["post_id", "importance", "summary_he", "analysis_he"],
            "additionalProperties": False}}},
        "required": ["picks"],
        "additionalProperties": False,
    }


def user_prompt(posts: list[Post]) -> str:
    return json.dumps([{"post_id": p.id, "author": p.author, "time": p.created_at, "text": p.text}
                       for p in posts], ensure_ascii=False, indent=1)


WEEKEND_NOTE = ("It is the weekend and US markets are closed: rate 4 or 5 only what is dramatic, "
                "or what the investor needs to know before the coming trading week (events, data "
                "and earnings due, big news from the weekend).")


def is_weekend(now: datetime) -> bool:
    """Saturday or Sunday in New York (the owner, 2026-09-28: on weekends only the dramatic
    and what matters for the coming week)."""
    return now.astimezone(ZoneInfo("America/New_York")).weekday() >= 5


def triage(posts: list[Post], llm, min_importance: int, note: str = "") -> tuple[list[tuple[Post, dict]], int]:
    """(the picks at or above `min_importance`, in post order; how many answers were rejected)."""
    if not posts:
        return [], 0
    user = (note + "\n\n" if note else "") + user_prompt(posts)
    answer, _ = llm.complete(system=SYSTEM, user=user, schema=schema())
    by_id = {p.id: p for p in posts}
    picks: dict[str, dict] = {}
    rejected = 0
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
        if importance >= min_importance:
            analysis = str(item.get("analysis_he", "")).strip()
            if banned(analysis):             # advice or forecast wording: the news goes without it
                analysis = ""
            picks[post_id] = {"importance": importance, "summary_he": summary[:SUMMARY_MAX],
                              "analysis_he": analysis[:ANALYSIS_MAX]}
    return [(p, picks[p.id]) for p in posts if p.id in picks], rejected


def item(post: Post, pick_: dict, *, with_image: bool = False) -> str:
    """One pick in Telegram HTML: mark, account, the sentence and a link; then the short
    analysis, and under a photo the line that says what the picture shows."""
    mark = {5: "🔴", 4: "🟠"}.get(pick_["importance"], "🔵")
    text = (f"{mark} <b>{html.escape(post.author)}</b>: {html.escape(pick_['summary_he'])} "
            f'<a href="{html.escape(post.url, quote=True)}">↗</a>')
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
    """The picks without a photo in one message, then each photo with its line as the
    caption (under Telegram's 1024). A photo Telegram cannot fetch goes as text instead.
    Returns how many went with a photo."""
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


def run_once(*, accounts: list[str], source: XSource, llm_factory: Callable[[], Any], send: Callable[[str], None],
             state_path: Path, now: datetime, min_importance: int, daily_read_cap: int,
             weekend_min_importance: int = 4,
             llm_daily_cap: int = 45, llm_min_interval_s: int = 1200,
             send_photo: Callable[[str, str], None] | None = None,
             fetch: Callable[[str], tuple[str, bytes] | None] = fetch_image,
             first_lookback_s: int = 900, with_replies: bool = False) -> dict[str, Any]:
    """Read, pick, send, remember. Returns counts only (safe for the public log).

    Posts are read every run, but Claude is asked at most every `llm_min_interval_s`
    and `llm_daily_cap` times a day (the subscription is shared with the chart
    analyses); the posts in between wait in the state's "pending" list and are
    judged together. The state is saved only after the message went out, so a failed
    send is retried by the next run and a sent post is never sent again."""
    state = load_state(state_path)
    stamp = int(now.timestamp())
    day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    reads = state.get("reads") if isinstance(state.get("reads"), dict) else {}
    if reads.get("day") != day:
        reads = {"day": day, "posts": 0}
    model = state.get("llm") if isinstance(state.get("llm"), dict) else {}
    if model.get("day") != day:
        model = {"day": day, "calls": 0, "last": model.get("last", 0), "paused_until": model.get("paused_until", 0)}
    summary: dict[str, Any] = {"accounts": len(accounts), "read": 0, "new": 0, "waiting": 0,
                               "judged": 0, "sent": 0, "rejected": 0, "calls": 0,
                               "claude_today": model["calls"]}
    if not accounts:
        summary["status"] = "no accounts"
        return summary
    seen = list(state.get("seen") or [])
    pending = [r for r in state.get("pending") or []
               if isinstance(r, dict) and stamp - int(r.get("added", 0)) <= PENDING_MAX_AGE_S]
    since = max(int(state.get("since") or stamp - first_lookback_s), stamp - MAX_LOOKBACK_S)
    if reads["posts"] < daily_read_cap:
        posts = source.new_posts(accounts, since - OVERLAP_S, with_replies=with_replies)
        summary["calls"], summary["read"] = source.calls, len(posts)
        seen_set = set(seen)
        new = [p for p in posts if p.id not in seen_set]
        summary["new"] = len(new)
        seen = (seen + [p.id for p in new])[-SENT_KEPT:]
        pending = (pending + [_post_row(p, stamp) for p in new])[-PENDING_MAX:]
        reads["posts"] += len(posts)
        since = stamp
    else:
        summary["read_status"] = "daily cap"
    status = "ok"
    due = (pending and model["calls"] < llm_daily_cap and stamp >= int(model.get("paused_until", 0))
           and stamp - int(model.get("last", 0)) >= llm_min_interval_s)
    if due:
        try:
            llm = llm_factory()
            weekend = is_weekend(now)
            picks, summary["rejected"] = triage(
                [_row_post(r) for r in pending], llm,
                max(min_importance, weekend_min_importance) if weekend else min_importance,
                WEEKEND_NOTE if weekend else "")
        except UsageLimit:
            model["paused_until"] = stamp + LIMIT_PAUSE_S
            picks, status = [], "claude limit"
        else:
            model["calls"] += 1
            model["last"] = stamp
            summary["judged"] = len(pending)
            pending = []
        if send_photo is not None and any(p.photo for p, _ in picks):
            try:                             # a second look, at the pictures: never blocks the news
                summary["explained"] = explain_images(picks, llm, fetch)
                model["calls"] += 1
            except UsageLimit:
                model["paused_until"] = stamp + LIMIT_PAUSE_S
            except ProviderError as exc:
                summary["explain_error"] = type(exc).__name__
        if picks:
            summary["photos"] = deliver(picks, send, send_photo)
        summary["sent"] = len(picks)
    elif pending and model["calls"] >= llm_daily_cap:
        status = "claude daily cap"
    summary["waiting"] = len(pending)
    summary["claude_today"] = model["calls"]
    save_state(state_path, {"since": since, "seen": seen, "pending": pending, "reads": reads, "llm": model})
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
