"""Tests for the deterministic signal schema and causal timestamps."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.domain.enums import Action, SignalReasonCode
from quantora_trade.domain.models import Candle
from quantora_trade.strategy.configuration import (
    StrategyConfiguration,
    StrategyParameterOverride,
)
from quantora_trade.strategy.signals import build_configured_signal, build_signal

OPEN_TIME = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)


def candle(*, symbol: str = "EURUSD", is_closed: bool = True) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe="M15",
        open_time=OPEN_TIME,
        close_time=OPEN_TIME + timedelta(minutes=15),
        open=Decimal("1.1000"),
        high=Decimal("1.1020"),
        low=Decimal("1.0990"),
        close=Decimal("1.1010"),
        tick_volume=100,
        is_closed=is_closed,
    )


def test_signal_contains_complete_schema_and_canonical_reason_codes() -> None:
    result = build_signal(
        candle=candle(),
        action=Action.BUY,
        confidence=Decimal("0.74"),
        strategy_version="technical-v1",
        reason_codes=(
            SignalReasonCode.RSI_BULLISH_CONFIRMATION,
            SignalReasonCode.EMA_BULLISH_ALIGNMENT,
            SignalReasonCode.RSI_BULLISH_CONFIRMATION,
        ),
        ttl_bars=2,
    )

    assert result.symbol == "EURUSD"
    assert result.timeframe == "M15"
    assert result.action is Action.BUY
    assert result.reason_codes == (
        "EMA_BULLISH_ALIGNMENT",
        "RSI_BULLISH_CONFIRMATION",
    )
    assert result.observed_at == candle().close_time
    assert result.expires_at == candle().close_time + timedelta(minutes=30)


def test_signal_is_deterministic_for_same_inputs() -> None:
    arguments = {
        "candle": candle(symbol="XAUUSD"),
        "action": Action.HOLD,
        "confidence": Decimal("0.40"),
        "strategy_version": "technical-v1",
        "reason_codes": (SignalReasonCode.INSUFFICIENT_EVIDENCE,),
    }

    first = build_signal(**arguments)  # type: ignore[arg-type]
    second = build_signal(**arguments)  # type: ignore[arg-type]

    assert first == second
    assert first.id == second.id


def test_signal_rejects_forming_candle_and_future_evidence() -> None:
    common = {
        "action": Action.SELL,
        "confidence": Decimal("0.68"),
        "strategy_version": "technical-v1",
        "reason_codes": (SignalReasonCode.EMA_BEARISH_ALIGNMENT,),
    }

    with pytest.raises(ValueError, match="closed candle"):
        build_signal(candle=candle(is_closed=False), **common)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="future"):
        build_signal(
            candle=candle(),
            evidence_observed_at=(candle().close_time + timedelta(seconds=1),),
            **common,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        build_signal(
            candle=candle(),
            evidence_observed_at=(datetime(2026, 8, 16, 8, 15),),
            **common,  # type: ignore[arg-type]
        )


def test_signal_rejects_unknown_timeframe_and_missing_reasons() -> None:
    invalid_timeframe = replace(candle(), timeframe="D1")
    with pytest.raises(ValueError, match="unsupported signal timeframe"):
        build_signal(
            candle=invalid_timeframe,
            action=Action.HOLD,
            confidence=Decimal("0"),
            strategy_version="technical-v1",
            reason_codes=(SignalReasonCode.INSUFFICIENT_EVIDENCE,),
        )
    with pytest.raises(ValueError, match="reason code"):
        build_signal(
            candle=candle(),
            action=Action.HOLD,
            confidence=Decimal("0"),
            strategy_version="technical-v1",
            reason_codes=(),
        )


def test_configured_signal_applies_symbol_threshold_and_ttl() -> None:
    configuration = StrategyConfiguration(
        version="technical-v2",
        symbol_overrides={
            "XAUUSD": StrategyParameterOverride(
                minimum_confidence=Decimal("0.75"),
                signal_ttl_bars=2,
            )
        },
    )

    result = build_configured_signal(
        candle=candle(symbol="XAUUSD"),
        proposed_action=Action.BUY,
        confidence=Decimal("0.70"),
        configuration=configuration,
        reason_codes=(SignalReasonCode.EMA_BULLISH_ALIGNMENT,),
    )

    assert result.action is Action.HOLD
    assert result.reason_codes == ("BELOW_MINIMUM_CONFIDENCE",)
    assert result.strategy_version == "technical-v2"
    assert result.expires_at == candle().close_time + timedelta(minutes=30)


def test_configured_signal_keeps_direction_when_threshold_passes() -> None:
    configuration = StrategyConfiguration(version="technical-v2")

    result = build_configured_signal(
        candle=candle(),
        proposed_action=Action.SELL,
        confidence=Decimal("0.68"),
        configuration=configuration,
        reason_codes=(SignalReasonCode.EMA_BEARISH_ALIGNMENT,),
    )

    assert result.action is Action.SELL
    assert result.reason_codes == ("EMA_BEARISH_ALIGNMENT",)
