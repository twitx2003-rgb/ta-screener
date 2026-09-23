"""rules.yaml -> checked, typed access. Detectors never hard-code a threshold.

Every parameter must say whether its number is Bulkowski's (`origin: site`) or
our own choice (`origin: ours`), so the website can show which is which. A
parameter a detector asks for that the file does not define is an error, not a
default.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError

RULES_PATH = Path(__file__).with_name("rules.yaml")
ORIGINS = ("site", "ours")
DIRECTIONS = ("bullish", "bearish", "either")


@dataclass(frozen=True)
class Param:
    value: float
    origin: str
    note: str = ""


@dataclass(frozen=True)
class PatternRules:
    key: str
    family: str                  # chart | candle
    name_he: str
    name_en: str
    direction: str
    url: str
    rules_he: tuple[str, ...]
    params: dict[str, Param]

    def p(self, name: str) -> float:
        if name not in self.params:
            raise ConfigError(f"rules.yaml: pattern '{self.key}' has no parameter '{name}'")
        return self.params[name].value


@dataclass(frozen=True)
class Rules:
    general: dict[str, Param]
    candle_general: dict[str, Param]
    chart: dict[str, PatternRules]
    candle: dict[str, PatternRules]
    digest: str                  # identifies the rule set a scan used
    confirmed_from_book: bool = False   # every number checked against the books?

    def g(self, name: str) -> float:
        if name not in self.general:
            raise ConfigError(f"rules.yaml: no general parameter '{name}'")
        return self.general[name].value

    def cg(self, name: str) -> float:
        if name not in self.candle_general:
            raise ConfigError(f"rules.yaml: no candle parameter '{name}'")
        return self.candle_general[name].value

    def pattern(self, key: str) -> PatternRules:
        found = self.chart.get(key) or self.candle.get(key)
        if found is None:
            raise ConfigError(f"rules.yaml: no pattern '{key}'")
        return found


def _params(raw: Any, where: str) -> dict[str, Param]:
    if not isinstance(raw, dict):
        raise ConfigError(f"rules.yaml: {where} must be a mapping")
    out = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict) or "value" not in spec or "origin" not in spec:
            raise ConfigError(f"rules.yaml: {where}.{name} needs 'value' and 'origin'")
        if spec["origin"] not in ORIGINS:
            raise ConfigError(f"rules.yaml: {where}.{name}.origin must be one of {ORIGINS}")
        unknown = set(spec) - {"value", "origin", "note"}
        if unknown:
            raise ConfigError(f"rules.yaml: {where}.{name} has unknown key(s) {sorted(unknown)}")
        try:
            value = float(spec["value"])   # YAML 1.1 reads 1.0e9 as a string; convert
        except (TypeError, ValueError):
            raise ConfigError(f"rules.yaml: {where}.{name}.value is not a number") from None
        out[name] = Param(value, spec["origin"], str(spec.get("note", "")))
    return out


def _pattern(key: str, raw: Any, family: str) -> PatternRules:
    where = f"{family}.{key}"
    if not isinstance(raw, dict):
        raise ConfigError(f"rules.yaml: {where} must be a mapping")
    required = {"name_he", "name_en", "direction", "url", "rules_he"}
    missing = required - set(raw)
    if missing:
        raise ConfigError(f"rules.yaml: {where} is missing {sorted(missing)}")
    unknown = set(raw) - required - {"params"}
    if unknown:
        raise ConfigError(f"rules.yaml: {where} has unknown key(s) {sorted(unknown)}")
    if raw["direction"] not in DIRECTIONS:
        raise ConfigError(f"rules.yaml: {where}.direction must be one of {DIRECTIONS}")
    return PatternRules(key=key, family=family, name_he=raw["name_he"], name_en=raw["name_en"],
                        direction=raw["direction"], url=raw["url"],
                        rules_he=tuple(raw["rules_he"]),
                        params=_params(raw.get("params", {}), where + ".params"))


def load_rules(path: Path = RULES_PATH) -> Rules:
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ConfigError("rules.yaml must be a mapping")
    candle = raw.get("candle") or {}
    return Rules(
        general=_params(raw.get("general"), "general"),
        candle_general=_params(candle.get("general"), "candle.general"),
        chart={k: _pattern(k, v, "chart") for k, v in (raw.get("chart") or {}).items()},
        candle={k: _pattern(k, v, "candle") for k, v in (candle.get("patterns") or {}).items()},
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        confirmed_from_book=raw.get("confirmed_from_book") is True,
    )
