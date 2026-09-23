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


_SECTIONS = {
    "paths": PathSettings,
    "tradingview": TradingViewSettings,
    "market": MarketSettings,
}


@dataclass(frozen=True)
class Settings:
    root: Path
    paths: PathSettings = field(default_factory=PathSettings)
    tradingview: TradingViewSettings = field(default_factory=TradingViewSettings)
    market: MarketSettings = field(default_factory=MarketSettings)

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
