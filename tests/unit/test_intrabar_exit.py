"""Tests for conservative SL/TP resolution on ambiguous OHLC bars."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from quantora_trade.backtesting.execution import ExecutionCostModel, SimulatedFill
from quantora_trade.backtesting.intrabar import (
    IntrabarExitReason,
    simulate_conservative_intrabar_exit,
)
from quantora_trade.backtesting.portfolio import PortfolioState
from quantora_trade.domain.enums import Action, AssetClass
from quantora_trade.domain.models import Candle, Instrument

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


def instrument() -> Instrument:
    return Instrument(
        symbol="XAUUSD",
        asset_class=AssetClass.METAL,
        quote_currency="USD",
        digits=2,
        point=Decimal("0.01"),
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        tick_value=Decimal("1"),
        contract_size=Decimal("100"),
        spread_points=2,
        session_timezone="UTC",
        session_profile="24x5",
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
    )


def costs() -> ExecutionCostModel:
    return ExecutionCostModel(
        point=Decimal("0.01"),
        spread_points=Decimal("2"),
        slippage_points=Decimal("1"),
        commission_per_side=Decimal("2"),
    )


def opening_fill(side: Action = Action.BUY) -> SimulatedFill:
    return SimulatedFill(
        id=uuid4(),
        signal_id=uuid4(),
        symbol="XAUUSD",
        side=side,
        executed_at=NOW,
        reference_price=Decimal("100"),
        fill_price=Decimal("100"),
        spread_price=Decimal("0"),
        slippage_price=Decimal("0"),
        commission=Decimal("2"),
        cost_scenario="base",
    )


def position(side: Action = Action.BUY):
    stop_loss = Decimal("99") if side is Action.BUY else Decimal("101")
    take_profit = Decimal("101") if side is Action.BUY else Decimal("99")
    portfolio = PortfolioState(cash_balance=Decimal("1000")).open_position(
        fill=opening_fill(side),
        volume=Decimal("1"),
        instrument=instrument(),
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
    return portfolio.positions[0]


def candle(*, open_: str, high: str, low: str, close: str) -> Candle:
    return Candle(
        symbol="XAUUSD",
        timeframe="M15",
        open_time=NOW,
        close_time=NOW + timedelta(minutes=15),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        tick_volume=100,
        is_closed=True,
    )


def test_ambiguous_bar_uses_stop_first_conservative_outcome() -> None:
    result = simulate_conservative_intrabar_exit(
        position=position(),
        candle=candle(open_="100", high="102", low="98", close="100"),
        costs=costs(),
    )

    assert result is not None
    assert result.reason is IntrabarExitReason.STOP_LOSS
    assert result.ambiguous
    assert result.fill.reference_price == Decimal("99")
    assert result.fill.fill_price == Decimal("98.98")
    assert result.fill.executed_at == NOW + timedelta(minutes=15)


def test_gap_stop_fills_from_worse_open_and_take_profit_does_not_improve() -> None:
    stopped = simulate_conservative_intrabar_exit(
        position=position(),
        candle=candle(open_="98", high="99", low="97", close="98"),
        costs=costs(),
    )
    targeted = simulate_conservative_intrabar_exit(
        position=position(),
        candle=candle(open_="102", high="103", low="101.5", close="102"),
        costs=costs(),
    )

    assert stopped is not None
    assert stopped.reason is IntrabarExitReason.GAP_STOP_LOSS
    assert stopped.fill.executed_at == NOW
    assert stopped.fill.reference_price == Decimal("98")
    assert stopped.fill.fill_price == Decimal("97.98")
    assert targeted is not None
    assert targeted.reason is IntrabarExitReason.GAP_TAKE_PROFIT
    assert targeted.fill.executed_at == NOW
    assert targeted.fill.reference_price == Decimal("101")


def test_bar_without_trigger_returns_none() -> None:
    result = simulate_conservative_intrabar_exit(
        position=position(),
        candle=candle(open_="100", high="100.5", low="99.5", close="100.2"),
        costs=costs(),
    )

    assert result is None


def test_ambiguous_sell_bar_also_uses_stop_first() -> None:
    result = simulate_conservative_intrabar_exit(
        position=position(Action.SELL),
        candle=candle(open_="100", high="102", low="98", close="100"),
        costs=costs(),
    )

    assert result is not None
    assert result.reason is IntrabarExitReason.STOP_LOSS
    assert result.ambiguous
    assert result.fill.side is Action.BUY
    assert result.fill.reference_price == Decimal("101")
    assert result.fill.fill_price == Decimal("101.02")
