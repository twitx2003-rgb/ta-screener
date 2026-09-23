"""Reading the discussion channels for the site (written by tascreen/channels)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from ..channels.generate import Persona, load_personas
from ..channels.select import GENERAL, Channel, channels
from ..config import ChannelsSettings
from ..patterns.rules import Rules
from ..store import Store
from .data import ScanView


class ChannelRepository:
    def __init__(self, store: Store, rules: Rules, cfg: ChannelsSettings,
                 personas: dict[str, Persona] | None = None):
        self.store, self.rules, self.cfg = store, rules, cfg
        self.personas = personas or load_personas()

    def channel_list(self, view: ScanView | None) -> list[Channel]:
        if view is None:
            return [Channel(GENERAL, "#כללי", "כללי", 0)]
        return channels(view.detections, self.rules, self.cfg.count)

    def known(self, channel_id: str) -> bool:
        return channel_id == GENERAL or channel_id in self.rules.chart or channel_id in self.rules.candle

    def _days(self) -> list[date]:
        return self.store.channel_days()[-self.cfg.days_shown:]

    def fresh(self) -> set[str]:
        """Channels with a thread in the newest channel day (the sidebar's dot)."""
        days = self._days()
        if not days:
            return set()
        out = set()
        folder = self.store.channels_dir / days[-1].isoformat()
        for path in folder.glob("*.json"):
            if path.stem == "live":
                doc = self.store.read_channel_doc(days[-1], "live") or {}
                out |= {t["channel"] for t in doc.get("threads", [])}
            else:
                doc = self.store.read_channel_doc(days[-1], path.stem) or {}
                if doc.get("threads"):
                    out.add(path.stem)
        return out

    def threads(self, channel_id: str) -> list[dict[str, Any]]:
        """The channel's threads over the last `days_shown` days, oldest first, each
        post with its persona and its chart's SVG markup."""
        out = []
        for day in self._days():
            daily = self.store.read_channel_doc(day, channel_id) or {}
            live = self.store.read_channel_doc(day, "live") or {}
            found = list(daily.get("threads", []))
            found += [t for t in live.get("threads", []) if t.get("channel") == channel_id]
            for thread in found:
                posts = []
                for post in thread.get("posts", []):
                    persona = self.personas.get(post.get("persona"))
                    if persona is None:
                        continue
                    svg = self.store.read_channel_chart(day, post["chart"]) if post.get("chart") else None
                    posts.append({**post, "who": asdict(persona), "svg": svg})
                if posts:
                    out.append({**thread, "day": day, "posts": posts})
        return sorted(out, key=lambda t: t.get("created_at", ""))

    def stamp(self) -> str:
        """Changes whenever a channel file is written (the pages poll it)."""
        days = self._days()
        if not days:
            return ""
        folder = self.store.channels_dir / days[-1].isoformat()
        times = [p.stat().st_mtime_ns for p in folder.glob("*.json")]
        return f"{days[-1].isoformat()}:{max(times) if times else 0}"
