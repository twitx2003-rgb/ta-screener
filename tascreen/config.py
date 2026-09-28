"""Settings from config.yaml, resolved once and passed around explicitly.

Unknown keys are rejected, at the top level and in every section: a typo in
config.yaml is otherwise a silent bug.
"""
from __future__ import annotations

import os

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class PathSettings:
    data: str = "data"
    logs: str = "logs"


@dataclass(frozen=True)
class TradingViewSettings:
    url: str = "https://mcp.tradingview.com/mcp"
    # Holds a refresh token, so it lives in the user folder, outside the repo.
    # Deliberately not market-research-pipeline's file: each project registers its
    # own OAuth client, so one project's token refresh never signs the other out.
    token_path: str = "~/.ta-screener/tv_tokens.json"
    callback_host: str = "localhost"
    callback_port: int = 8766
    rate_limit_delays: tuple[float, ...] = (5.0, 15.0, 45.0)

    def __post_init__(self):
        object.__setattr__(self, "rate_limit_delays",
                           tuple(float(d) for d in self.rate_limit_delays))
        if self.callback_host != "localhost":
            # TradingView's CDN blocked the sign-in page when the redirect carried
            # an IP literal (market-research-pipeline, third live sign-in).
            raise ConfigError("tradingview.callback_host must be 'localhost'")


@dataclass(frozen=True)
class MarketSettings:
    timezone: str = "America/New_York"
    # A daily bar counts as a close only after this New York time.
    session_close: str = "16:15"

    def __post_init__(self):
        try:
            hour, minute = (int(p) for p in self.session_close.split(":"))
        except ValueError:
            raise ConfigError(f"market.session_close must be HH:MM, got '{self.session_close}'") from None
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ConfigError(f"market.session_close out of range: '{self.session_close}'")


@dataclass(frozen=True)
class UniverseSettings:
    market: str = "america"
    min_market_cap: float = 1e9
    # Starting market-cap bands (USD). Each call returns at most `row_cap` rows and
    # there is no offset, so each band must hold fewer; a band that holds more is
    # split in two automatically. The last band is open-ended.
    band_edges: tuple[float, ...] = (1e9, 1.5e9, 2e9, 3e9, 5e9, 1e10, 3e10, 1e11)
    # The tool caps rows at 1000, but the MCP server also refuses results over
    # 1,000,000 bytes, which live came to roughly 430 default rows. A band whose
    # answer is too big is split as well, so this only avoids wasted calls.
    row_cap: int = 400
    # Symbol prefixes (exchanges) dropped after the fetch; the screener cannot
    # filter on them. Its $1B "stock" results include OTC listings.
    drop_exchanges: tuple[str, ...] = ("OTC",)
    # `subtype` values dropped after the fetch. Live subtypes above $1B were
    # "common" and "preferred"; a preferred issue carries its parent's market cap
    # and trades like a bond, so chart patterns on it mean little.
    drop_subtypes: tuple[str, ...] = ("preferred",)
    # When the screener is rate limited, `--update` may go on with the newest saved
    # universe if it is at most this many days old.
    max_age_days: float = 7

    def __post_init__(self):
        # PyYAML (YAML 1.1) reads `1.0e9` without a sign as a *string*; sent as the
        # filter floor, the screener silently ignored it (live: 11803 rows instead
        # of ~4000). Every number here is converted explicitly.
        object.__setattr__(self, "min_market_cap", float(self.min_market_cap))
        object.__setattr__(self, "max_age_days", float(self.max_age_days))
        object.__setattr__(self, "row_cap", int(self.row_cap))
        object.__setattr__(self, "band_edges", tuple(float(e) for e in self.band_edges))
        object.__setattr__(self, "drop_exchanges", tuple(self.drop_exchanges))
        object.__setattr__(self, "drop_subtypes", tuple(self.drop_subtypes))
        edges = self.band_edges
        if not edges or edges[0] != float(self.min_market_cap):
            raise ConfigError("universe.band_edges must start at universe.min_market_cap")
        if any(b <= a for a, b in zip(edges, edges[1:])):
            raise ConfigError("universe.band_edges must be strictly increasing")
        if not 1 <= self.row_cap <= 1000:
            raise ConfigError("universe.row_cap must be 1..1000 (the screener's cap)")


@dataclass(frozen=True)
class BarsSettings:
    # Daily bars fetched for a symbol seen for the first time (~2.4 years:
    # enough for the 150-day average, the EMA200 cross-check, and patterns that
    # take up to a year).
    history: int = 600
    # Bars re-fetched on top of what is stored; they must match what is stored,
    # or the whole history is fetched again (a split changes every past price).
    overlap: int = 5
    overlap_tolerance_pct: float = 0.05
    # A new MCP session every this many symbols (access tokens last 900 s).
    session_batch: int = 100
    # Calls in flight at once inside a session, and a pause after each call.
    concurrency: int = 1
    min_interval_s: float = 0.0

    def __post_init__(self):
        for name in ("history", "overlap", "session_batch", "concurrency"):
            object.__setattr__(self, name, int(getattr(self, name)))
        for name in ("overlap_tolerance_pct", "min_interval_s"):
            object.__setattr__(self, name, float(getattr(self, name)))
        if self.history < 250 or self.history > 5000:
            raise ConfigError("bars.history must be 250..5000 (get-ohlcv's cap is 5000)")
        if self.overlap < 2:
            raise ConfigError("bars.overlap must be at least 2")
        if self.session_batch < 1 or self.concurrency < 1:
            raise ConfigError("bars.session_batch and bars.concurrency must be >= 1")




@dataclass(frozen=True)
class LiveSettings:
    # The session's breakout watch (run.py --ci-live) keeps going this long after the
    # close, because quotes are delayed.
    after_close_minutes: float = 20.0

    def __post_init__(self):
        object.__setattr__(self, "after_close_minutes", float(self.after_close_minutes))
        if not 0 <= self.after_close_minutes <= 120:
            raise ConfigError("live.after_close_minutes must be 0..120")


@dataclass(frozen=True)
class ClaudeSettings:
    # Claude Code for the chart analyst (the owner's subscription; `claude -p` must run
    # from a normal terminal or a workflow, not from inside Claude Code).
    model: str = "sonnet"
    timeout_s: int = 600

    def __post_init__(self):
        object.__setattr__(self, "timeout_s", int(self.timeout_s))
        if self.timeout_s < 30:
            raise ConfigError("claude.timeout_s must be at least 30")


@dataclass(frozen=True)
class OutcomesSettings:
    # What happened after each breakout (tascreen/outcomes.py): tracked this many
    # sessions, then "no decision" (expired).
    max_sessions: int = 60
    # The scorecard shows percentages only for patterns with at least this many
    # decided breakouts; below it, counts only.
    min_cases: int = 20
    # run.py --backfill-outcomes: the detector run on past bars, cut at every
    # `backfill_step`-th session (1 = exactly what daily scans would have seen), from
    # the first session with `backfill_min_bars` of history, in this many processes.
    backfill_step: int = 1
    backfill_min_bars: int = 150
    backfill_workers: int = 4

    def __post_init__(self):
        for name in ("max_sessions", "min_cases", "backfill_step", "backfill_min_bars",
                     "backfill_workers"):
            object.__setattr__(self, name, int(getattr(self, name)))
        if not 5 <= self.max_sessions <= 250:
            raise ConfigError("outcomes.max_sessions must be 5..250")
        if not 1 <= self.min_cases <= 1000:
            raise ConfigError("outcomes.min_cases must be 1..1000")
        if not 1 <= self.backfill_step <= 20:
            raise ConfigError("outcomes.backfill_step must be 1..20")
        if not 60 <= self.backfill_min_bars <= 500:
            raise ConfigError("outcomes.backfill_min_bars must be 60..500")
        if not 1 <= self.backfill_workers <= 32:
            raise ConfigError("outcomes.backfill_workers must be 1..32")


@dataclass(frozen=True)
class AlertsSettings:
    # Telegram alerts on bullish chart-pattern breakouts (tascreen/alerts.py; owner's
    # request 2026-09-24): an evening report after the close, and crossings during the
    # session (run.py --ci-live).
    enabled: bool = True
    verge_pct: float = 2.0             # "on the verge": the close this far below the breakout line
    watch_pct: float = 5.0             # watched in the session: the line this far above the close
    top_analyses: int = 3              # full analyses (analyst.yml) for the strongest breakouts
    live_interval_minutes: float = 5.0
    # stocks farther than verge_pct from their line need a bigger move to cross it: they
    # are priced every `far_every`-th pass only (fewer calls; TradingView slows down)
    far_every: int = 3
    live_max_symbols: int = 120        # get-ohlcv calls per pass, at most
    # Big moves before the open (tascreen/premarket.py; owner, 2026-09-28): 07:30, 08:30
    # and 09:15 New York time, the $1B+ stocks moving at least this much on real volume
    premarket: bool = True
    premarket_min_pct: float = 4.0
    premarket_min_volume: float = 300000.0

    def __post_init__(self):
        for name in ("verge_pct", "watch_pct", "live_interval_minutes", "premarket_min_pct",
                     "premarket_min_volume"):
            object.__setattr__(self, name, float(getattr(self, name)))
        for name in ("top_analyses", "live_max_symbols", "far_every"):
            object.__setattr__(self, name, int(getattr(self, name)))
        if not 1 <= self.far_every <= 6:
            raise ConfigError("alerts.far_every must be 1..6")
        if not 0 < self.verge_pct <= 10:
            raise ConfigError("alerts.verge_pct must be above 0 and at most 10")
        if not self.verge_pct <= self.watch_pct <= 20:
            raise ConfigError("alerts.watch_pct must be verge_pct..20")
        if not 0 <= self.top_analyses <= 5:
            raise ConfigError("alerts.top_analyses must be 0..5 (they use the daily analyses)")
        if not 5 <= self.live_interval_minutes <= 60:
            raise ConfigError("alerts.live_interval_minutes must be 5..60")
        if not 1 <= self.live_max_symbols <= 300:
            raise ConfigError("alerts.live_max_symbols must be 1..300")
        if not 1 <= self.premarket_min_pct <= 50:
            raise ConfigError("alerts.premarket_min_pct must be 1..50")
        if self.premarket_min_volume < 0:
            raise ConfigError("alerts.premarket_min_volume must be 0 or more")


@dataclass(frozen=True)
class XNewsSettings:
    # Breaking Wall Street news from X accounts the owner chose (tascreen/xnews.py;
    # owner's request 2026-09-27): Claude picks what matters, the bot sends a Hebrew summary.
    enabled: bool = True
    until: str = "2026-12-27"          # the owner asked for three months; after it, one notice and silence
    accounts: tuple = ()
    min_importance: int = 3            # 1-5; 3 = a useful Wall Street update (owner, 2026-09-28)
    weekend_min_importance: int = 4    # Saturday and Sunday: only the dramatic, and what matters for the week
    daily_read_cap: int = 2000         # posts read a day, at most (the reader is paid per post)
    with_replies: bool = False
    # Claude is shared with the chart analyses: the posts are read every
    # run, but judged together at most every `claude_every_minutes`, `claude_daily_cap` a day
    claude_daily_cap: int = 45
    claude_every_minutes: int = 30
    model: str = "sonnet"
    effort: str = "low"
    timeout_s: int = 300

    def __post_init__(self):
        from datetime import date

        from .xnews import account_name

        try:
            date.fromisoformat(str(self.until))
        except ValueError:
            raise ConfigError(f"xnews.until must be a YYYY-MM-DD date, got '{self.until}'") from None
        object.__setattr__(self, "until", str(self.until))
        raw = self.accounts or ()
        if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
            raise ConfigError("xnews.accounts must be a list of X account names")
        try:
            names = tuple(dict.fromkeys(account_name(a) for a in raw))
        except Exception as exc:
            raise ConfigError(f"xnews.accounts: {exc}") from None
        object.__setattr__(self, "accounts", names)
        for name in ("min_importance", "weekend_min_importance", "daily_read_cap", "timeout_s", "claude_daily_cap",
                     "claude_every_minutes"):
            object.__setattr__(self, name, int(getattr(self, name)))
        if not 1 <= self.min_importance <= 5:
            raise ConfigError("xnews.min_importance must be 1..5")
        if not 1 <= self.weekend_min_importance <= 5:
            raise ConfigError("xnews.weekend_min_importance must be 1..5")
        if not 20 <= self.daily_read_cap <= 50000:
            raise ConfigError("xnews.daily_read_cap must be 20..50000")
        if self.effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ConfigError("xnews.effort must be low, medium, high, xhigh or max")
        if self.timeout_s < 30:
            raise ConfigError("xnews.timeout_s must be at least 30")
        if not 1 <= self.claude_daily_cap <= 200:
            raise ConfigError("xnews.claude_daily_cap must be 1..200")
        if not 10 <= self.claude_every_minutes <= 180:
            raise ConfigError("xnews.claude_every_minutes must be 10..180")


_SECTIONS = {
    "paths": PathSettings,
    "tradingview": TradingViewSettings,
    "market": MarketSettings,
    "universe": UniverseSettings,
    "bars": BarsSettings,
    "live": LiveSettings,
    "claude": ClaudeSettings,
    "outcomes": OutcomesSettings,
    "alerts": AlertsSettings,
    "xnews": XNewsSettings,
}


@dataclass(frozen=True)
class Settings:
    root: Path
    paths: PathSettings = field(default_factory=PathSettings)
    tradingview: TradingViewSettings = field(default_factory=TradingViewSettings)
    market: MarketSettings = field(default_factory=MarketSettings)
    universe: UniverseSettings = field(default_factory=UniverseSettings)
    bars: BarsSettings = field(default_factory=BarsSettings)
    live: LiveSettings = field(default_factory=LiveSettings)
    claude: ClaudeSettings = field(default_factory=ClaudeSettings)
    outcomes: OutcomesSettings = field(default_factory=OutcomesSettings)
    alerts: AlertsSettings = field(default_factory=AlertsSettings)
    xnews: XNewsSettings = field(default_factory=XNewsSettings)

    @property
    def data_dir(self) -> Path:
        return self.root / self.paths.data

    @property
    def log_dir(self) -> Path:
        return self.root / self.paths.logs


def load_settings(config_path: Path | None = None, root: Path = ROOT) -> Settings:
    """config.yaml, then this machine's config.local.yaml next to it, if any (gitignored;
    e.g. the server's public host name). The local file replaces single keys, and the
    same checks apply to both."""
    path = config_path or (root / "config.yaml")
    if not path.exists():
        raise ConfigError(f"Missing config file: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml must be a mapping at the top level")
    local_path = path.with_name(path.stem + ".local.yaml")
    if local_path.exists():
        local = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        if not isinstance(local, dict) or not all(isinstance(v, dict) for v in local.values()):
            raise ConfigError(f"{local_path.name} must map sections to key: value mappings")
        for section, values in local.items():
            raw[section] = {**(raw.get(section) or {}), **values}
    # A separate TradingView sign-in (e.g. the one GitHub Actions uses) lives in its own
    # file; TA_TV_TOKEN_PATH names it, so one project never refreshes another's tokens.
    if os.environ.get("TA_TV_TOKEN_PATH"):
        raw["tradingview"] = {**(raw.get("tradingview") or {}),
                              "token_path": os.environ["TA_TV_TOKEN_PATH"]}

    unknown_sections = set(raw) - set(_SECTIONS)
    if unknown_sections:
        raise ConfigError(f"config.yaml: unknown section(s) {sorted(unknown_sections)}; "
                          f"known: {sorted(_SECTIONS)}")

    built: dict[str, Any] = {}
    for name, cls in _SECTIONS.items():
        section = raw.get(name) or {}
        if not isinstance(section, dict):
            raise ConfigError(f"config.yaml: '{name}' must be a mapping, "
                              f"got {type(section).__name__}")
        known = {f.name for f in fields(cls)}
        unknown = set(section) - known
        if unknown:
            raise ConfigError(f"config.yaml: unknown key(s) under '{name}': {sorted(unknown)}")
        built[name] = cls(**section)
    return Settings(root=root, **built)
