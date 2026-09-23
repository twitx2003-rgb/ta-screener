"""Settings from config.yaml, resolved once and passed around explicitly.

Unknown keys are rejected, at the top level and in every section: a typo in
config.yaml is otherwise a silent bug.
"""
from __future__ import annotations

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
    # enough for a 200-day average and patterns that take up to a year).
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
class WebSettings:
    # The site always binds to 127.0.0.1 (TradingView data may not be served to
    # others); the host is deliberately not a setting.
    port: int = 8050
    open_browser: bool = True
    rows_per_page: int = 100

    def __post_init__(self):
        object.__setattr__(self, "port", int(self.port))
        object.__setattr__(self, "rows_per_page", int(self.rows_per_page))
        if not 1024 <= self.port <= 65535:
            raise ConfigError("web.port must be 1024..65535")
        if not 10 <= self.rows_per_page <= 1000:
            raise ConfigError("web.rows_per_page must be 10..1000")


_SECTIONS = {
    "paths": PathSettings,
    "tradingview": TradingViewSettings,
    "market": MarketSettings,
    "universe": UniverseSettings,
    "bars": BarsSettings,
    "web": WebSettings,
}


@dataclass(frozen=True)
class Settings:
    root: Path
    paths: PathSettings = field(default_factory=PathSettings)
    tradingview: TradingViewSettings = field(default_factory=TradingViewSettings)
    market: MarketSettings = field(default_factory=MarketSettings)
    universe: UniverseSettings = field(default_factory=UniverseSettings)
    bars: BarsSettings = field(default_factory=BarsSettings)
    web: WebSettings = field(default_factory=WebSettings)

    @property
    def data_dir(self) -> Path:
        return self.root / self.paths.data

    @property
    def log_dir(self) -> Path:
        return self.root / self.paths.logs


def load_settings(config_path: Path | None = None, root: Path = ROOT) -> Settings:
    path = config_path or (root / "config.yaml")
    if not path.exists():
        raise ConfigError(f"Missing config file: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml must be a mapping at the top level")

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
