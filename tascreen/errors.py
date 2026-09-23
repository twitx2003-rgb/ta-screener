"""Typed failures. Every error this project raises on purpose derives from ScreenerError."""


class ScreenerError(Exception):
    """Base for every error this project raises deliberately."""


class ConfigError(ScreenerError):
    """config.yaml is missing something, or has a key nobody reads."""


class ProviderError(ScreenerError):
    """A data source failed, or returned something we refuse to trust."""


class ContractError(ScreenerError):
    """A data frame did not match its declared schema."""
