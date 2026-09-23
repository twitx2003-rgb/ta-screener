"""Data frame schemas.

Every frame this project stores is checked against a contract first, so a
provider change surfaces here as a named error rather than later as a confusing
KeyError or a silently wrong pattern.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .errors import ContractError

NUMERIC = "numeric"
DATETIME = "datetime"
STRING = "string"
ANY = "any"


@dataclass(frozen=True)
class Contract:
    name: str
    columns: dict[str, str]           # column -> NUMERIC | DATETIME | STRING | ANY
    required_non_null: tuple[str, ...] = ()
    allow_empty: bool = False

    def validate(self, df: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(df, pd.DataFrame):
            raise ContractError(f"{self.name}: expected a DataFrame, got {type(df).__name__}")

        missing = [c for c in self.columns if c not in df.columns]
        if missing:
            raise ContractError(
                f"{self.name}: missing column(s) {missing}. Present: {sorted(df.columns)}"
            )

        if df.empty and not self.allow_empty:
            raise ContractError(f"{self.name}: no rows")

        for column, kind in self.columns.items():
            series = df[column]
            if kind == NUMERIC and not pd.api.types.is_numeric_dtype(series):
                raise ContractError(
                    f"{self.name}.{column}: expected numeric, got dtype {series.dtype}"
                )
            if kind == DATETIME and not pd.api.types.is_datetime64_any_dtype(series):
                raise ContractError(
                    f"{self.name}.{column}: expected datetime, got dtype {series.dtype}"
                )

        for column in self.required_non_null:
            if df[column].isna().any():
                bad = int(df[column].isna().sum())
                raise ContractError(f"{self.name}.{column}: {bad} null value(s), none allowed")

        return df


BARS = Contract(
    name="bars",
    columns={
        "timestamp": DATETIME,
        "symbol": STRING,
        "open": NUMERIC,
        "high": NUMERIC,
        "low": NUMERIC,
        "close": NUMERIC,
        "volume": NUMERIC,
    },
    required_non_null=("timestamp", "open", "high", "low", "close"),
)


def canonical_timestamps(series: pd.Series) -> pd.Series:
    """One timestamp dtype everywhere: datetime64[ns, UTC].

    Fresh bars come as seconds with datetime.timezone.utc, Parquet gives back
    milliseconds with ZoneInfo("UTC"); pandas concatenates the two as `object`,
    which the BARS contract then refuses."""
    return pd.to_datetime(series, utc=True).dt.tz_convert("UTC").astype("datetime64[ns, UTC]")


UNIVERSE = Contract(
    name="universe",
    columns={
        "symbol": STRING,          # EXCHANGE:TICKER, as TradingView names it
        "exchange": STRING,
        "ticker": STRING,
        "description": ANY,
        "subtype": ANY,
        "sector": ANY,
        "industry": ANY,
        "currency": ANY,
        "market_cap": NUMERIC,
        "close": NUMERIC,
        "volume": NUMERIC,
        "avg_volume_10d": NUMERIC,
        "tv_rsi": NUMERIC,         # TradingView's own values, kept for cross-checks
        "tv_ema50": NUMERIC,
        "tv_ema200": NUMERIC,
        "tv_rating": NUMERIC,      # Recommend.All
        "next_earnings": ANY,
    },
    required_non_null=("symbol", "exchange", "ticker", "market_cap"),
)


PATTERNS = Contract(
    name="patterns",
    columns={
        "symbol": STRING, "family": STRING, "pattern": STRING, "direction": STRING,
        "status": STRING, "start": DATETIME, "end": DATETIME, "breakout_date": ANY,
        "breakout_price": NUMERIC, "height": NUMERIC, "target": NUMERIC,
        "volume_trend": STRING, "points_json": STRING, "lines_json": STRING,
        "checks_json": STRING,
    },
    required_non_null=("symbol", "family", "pattern", "direction", "status", "start", "end",
                       "checks_json"),
    allow_empty=True,             # a quiet day can have no pattern anywhere
)

INDICATORS = Contract(
    name="indicators",
    columns={
        "symbol": STRING, "last_date": STRING, "close": NUMERIC, "rsi14": NUMERIC,
        "sma50": NUMERIC, "sma200": NUMERIC, "atr_pct": NUMERIC, "rel_volume": NUMERIC,
        "pct_from_52w_high": NUMERIC, "market_cap": NUMERIC,
    },
    required_non_null=("symbol", "last_date", "close", "market_cap"),
)


def ohlcv_problem_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Bar rules as one boolean mask per rule (True = the bar breaks it)."""
    body_high = df[["open", "close"]].max(axis=1)
    body_low = df[["open", "close"]].min(axis=1)
    return {
        "high < max(open, close)": df["high"] < body_high - 1e-6,
        "low > min(open, close)": df["low"] > body_low + 1e-6,
        "negative volume": df["volume"] < 0,
        "non-positive price": (df[["open", "high", "low", "close"]] <= 0).any(axis=1),
    }


def assert_ohlcv_sane(df: pd.DataFrame) -> pd.DataFrame:
    """Bar-level invariants. Cheap here, and they catch provider bugs early."""
    if df.empty:
        return df
    problems = [rule for rule, mask in ohlcv_problem_masks(df).items() if mask.any()]
    if problems:
        raise ContractError(f"bars: impossible bars -> {'; '.join(problems)}")
    return df
