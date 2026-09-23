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
