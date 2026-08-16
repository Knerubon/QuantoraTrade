"""Validated global strategy configuration with explicit symbol overrides."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrategyParameters(BaseModel):
    """Fully resolved parameters used to produce a signal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum_confidence: Decimal = Field(default=Decimal("0.60"), ge=0, le=1)
    signal_ttl_bars: int = Field(default=1, gt=0, le=24)
    rsi_bullish_threshold: Decimal = Field(default=Decimal("55"), ge=0, le=100)
    rsi_bearish_threshold: Decimal = Field(default=Decimal("45"), ge=0, le=100)
    support_resistance_tolerance: Decimal = Field(default=Decimal("0.001"), gt=0, lt=1)

    @model_validator(mode="after")
    def validate_rsi_thresholds(self) -> "StrategyParameters":
        if self.rsi_bearish_threshold >= self.rsi_bullish_threshold:
            raise ValueError("bearish RSI threshold must be below bullish RSI threshold")
        return self


class StrategyParameterOverride(BaseModel):
    """Optional fields that override global parameters for one symbol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    signal_ttl_bars: int | None = Field(default=None, gt=0, le=24)
    rsi_bullish_threshold: Decimal | None = Field(default=None, ge=0, le=100)
    rsi_bearish_threshold: Decimal | None = Field(default=None, ge=0, le=100)
    support_resistance_tolerance: Decimal | None = Field(default=None, gt=0, lt=1)


class StrategyConfiguration(BaseModel):
    """Versioned strategy parameters resolved by canonical symbol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    global_parameters: StrategyParameters = Field(default_factory=StrategyParameters)
    symbol_overrides: dict[str, StrategyParameterOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identity(self) -> "StrategyConfiguration":
        if not self.version.strip():
            raise ValueError("strategy configuration version must not be empty")
        canonical = {symbol.strip().upper() for symbol in self.symbol_overrides}
        if any(not symbol.strip() for symbol in self.symbol_overrides):
            raise ValueError("override symbol must not be empty")
        if len(canonical) != len(self.symbol_overrides) or canonical != set(self.symbol_overrides):
            raise ValueError("override symbols must be unique canonical uppercase values")
        for symbol in self.symbol_overrides:
            self.resolve(symbol)
        return self

    def resolve(self, symbol: str) -> StrategyParameters:
        """Return a new immutable effective configuration without mutating globals."""

        canonical = symbol.strip().upper()
        if not canonical:
            raise ValueError("symbol must not be empty")
        override = self.symbol_overrides.get(canonical)
        if override is None:
            return self.global_parameters
        values = self.global_parameters.model_dump()
        values.update(override.model_dump(exclude_none=True))
        return StrategyParameters.model_validate(values)


def load_strategy_configuration(path: Path) -> StrategyConfiguration:
    """Load and validate a versioned strategy configuration from YAML."""

    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"cannot load strategy configuration: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError("strategy configuration root must be a mapping")
    return StrategyConfiguration.model_validate(payload)
