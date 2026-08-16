"""Tests for causal next-bar execution and explicit transaction costs."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.backtesting.execution import (
    ExecutionCostModel,
    simulate_next_bar_market_fill,
)
from quantora_trade.domain.enums import Action, SignalReasonCode
from quantora_trade.domain.models import Candle
from quantora_trade.strategy.signals import build_signal

OPEN_TIME = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)


def candle(*, open_time: datetime = OPEN_TIME, symbol: str = "EURUSD") -> Candle:
    return Candle(
        symbol=symbol,
        timeframe="M15",
        open_time=open_time,
        close_time=open_time + timedelta(minutes=15),
        open=Decimal("1.1000"),
        high=Decimal("1.1020"),
        low=Decimal("1.0990"),
        close=Decimal("1.1010"),
        tick_volume=100,
        is_closed=True,
    )


def signal(action: Action = Action.BUY):
    reason = (
        SignalReasonCode.INSUFFICIENT_EVIDENCE
        if action is Action.HOLD
        else (
            SignalReasonCode.EMA_BULLISH_ALIGNMENT
            if action is Action.BUY
            else SignalReasonCode.EMA_BEARISH_ALIGNMENT
        )
    )
    return build_signal(
        candle=candle(),
        action=action,
        confidence=Decimal("0.70"),
        strategy_version="technical-v1",
        reason_codes=(reason,),
    )


def costs() -> ExecutionCostModel:
    return ExecutionCostModel(
        point=Decimal("0.0001"),
        spread_points=Decimal("2"),
        slippage_points=Decimal("1"),
        commission_per_side=Decimal("3.50"),
    )


def test_buy_and_sell_receive_adverse_next_bar_costs() -> None:
    next_bar = candle(open_time=OPEN_TIME + timedelta(minutes=15))

    buy = simulate_next_bar_market_fill(signal=signal(Action.BUY), next_bar=next_bar, costs=costs())
    sell = simulate_next_bar_market_fill(
        signal=signal(Action.SELL), next_bar=next_bar, costs=costs()
    )

    assert buy.reference_price == Decimal("1.1000")
    assert buy.fill_price == Decimal("1.1002")
    assert sell.fill_price == Decimal("1.0998")
    assert buy.executed_at == next_bar.open_time
    assert buy.commission == Decimal("3.50")


def test_fill_is_deterministic_for_same_inputs() -> None:
    next_bar = candle(open_time=OPEN_TIME + timedelta(minutes=15))

    first = simulate_next_bar_market_fill(signal=signal(), next_bar=next_bar, costs=costs())
    second = simulate_next_bar_market_fill(signal=signal(), next_bar=next_bar, costs=costs())

    assert first == second
    assert first.id == second.id


def test_execution_rejects_look_ahead_identity_and_hold() -> None:
    before_observation = candle(open_time=OPEN_TIME)
    wrong_symbol = candle(open_time=OPEN_TIME + timedelta(minutes=15), symbol="XAUUSD")
    valid_next_bar = candle(open_time=OPEN_TIME + timedelta(minutes=15))

    with pytest.raises(ValueError, match="before the signal"):
        simulate_next_bar_market_fill(signal=signal(), next_bar=before_observation, costs=costs())
    with pytest.raises(ValueError, match="identity"):
        simulate_next_bar_market_fill(signal=signal(), next_bar=wrong_symbol, costs=costs())
    with pytest.raises(ValueError, match="HOLD"):
        simulate_next_bar_market_fill(
            signal=signal(Action.HOLD), next_bar=valid_next_bar, costs=costs()
        )


def test_execution_rejects_expired_signal_and_forming_bar() -> None:
    after_expiry = candle(open_time=OPEN_TIME + timedelta(minutes=45))
    forming = replace(candle(open_time=OPEN_TIME + timedelta(minutes=15)), is_closed=False)

    with pytest.raises(ValueError, match="expired"):
        simulate_next_bar_market_fill(signal=signal(), next_bar=after_expiry, costs=costs())
    with pytest.raises(ValueError, match="closed next bar"):
        simulate_next_bar_market_fill(signal=signal(), next_bar=forming, costs=costs())


def test_cost_model_rejects_unsafe_values() -> None:
    with pytest.raises(ValueError, match="point"):
        replace(costs(), point=Decimal("0"))
    with pytest.raises(ValueError, match="slippage"):
        replace(costs(), slippage_points=Decimal("-1"))
