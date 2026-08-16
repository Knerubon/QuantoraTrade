"""Tests for global strategy parameters and per-symbol overrides."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from quantora_trade.strategy.configuration import (
    StrategyConfiguration,
    StrategyParameterOverride,
    StrategyParameters,
    load_strategy_configuration,
)


def test_configuration_resolves_global_and_symbol_specific_parameters() -> None:
    configuration = StrategyConfiguration(
        version="technical-v1",
        global_parameters=StrategyParameters(minimum_confidence=Decimal("0.60")),
        symbol_overrides={
            "XAUUSD": StrategyParameterOverride(
                minimum_confidence=Decimal("0.72"),
                signal_ttl_bars=2,
            )
        },
    )

    eurusd = configuration.resolve("EURUSD")
    xauusd = configuration.resolve("xauusd")

    assert eurusd.minimum_confidence == Decimal("0.60")
    assert xauusd.minimum_confidence == Decimal("0.72")
    assert xauusd.signal_ttl_bars == 2
    assert xauusd.rsi_bullish_threshold == eurusd.rsi_bullish_threshold


def test_configuration_resolution_is_deterministic_and_does_not_mutate_global() -> None:
    configuration = StrategyConfiguration(
        version="technical-v1",
        symbol_overrides={"XAUUSD": StrategyParameterOverride(minimum_confidence=Decimal("0.70"))},
    )

    assert configuration.resolve("XAUUSD") == configuration.resolve("XAUUSD")
    assert configuration.global_parameters.minimum_confidence == Decimal("0.60")


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"version": ""}, "version"),
        (
            {
                "version": "v1",
                "symbol_overrides": {"xauusd": {}},
            },
            "canonical uppercase",
        ),
        (
            {
                "version": "v1",
                "global_parameters": {
                    "rsi_bearish_threshold": "60",
                    "rsi_bullish_threshold": "55",
                },
            },
            "bearish RSI",
        ),
    ],
)
def test_configuration_rejects_invalid_values(values: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        StrategyConfiguration.model_validate(values)


def test_configuration_rejects_invalid_effective_override() -> None:
    with pytest.raises(ValidationError, match="bearish RSI"):
        StrategyConfiguration(
            version="v1",
            symbol_overrides={
                "EURUSD": StrategyParameterOverride(rsi_bearish_threshold=Decimal("60"))
            },
        )


def test_example_yaml_loads_and_resolves_multi_symbol_configuration() -> None:
    configuration = load_strategy_configuration(Path("config/strategies.example.yaml"))

    assert configuration.version == "technical-v1"
    assert configuration.resolve("XAUUSD").minimum_confidence == Decimal("0.72")
    assert configuration.resolve("EURUSD").minimum_confidence == Decimal("0.65")
    assert configuration.resolve("AUDUSD").minimum_confidence == Decimal("0.60")


def test_loader_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="root must be a mapping"):
        load_strategy_configuration(path)
