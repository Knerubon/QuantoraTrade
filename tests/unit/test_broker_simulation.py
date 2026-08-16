"""Tests for deterministic volume, margin, partial-fill, and swap assumptions."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.backtesting.broker import (
    BrokerSimulationModel,
    FillReason,
    FillStatus,
    calculate_swap_cost,
    simulate_broker_fill_decision,
)
from quantora_trade.domain.enums import Action, AssetClass
from quantora_trade.domain.models import Instrument

MONDAY = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def instrument() -> Instrument:
    return Instrument(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        quote_currency="USD",
        digits=5,
        point=Decimal("0.00001"),
        pip_size=Decimal("0.0001"),
        tick_size=Decimal("0.00001"),
        tick_value=Decimal("1"),
        contract_size=Decimal("100000"),
        spread_points=2,
        session_timezone="UTC",
        session_profile="24x5",
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
    )


def test_full_fill_reserves_explicit_broker_margin() -> None:
    decision = simulate_broker_fill_decision(
        requested_volume=Decimal("1"),
        available_margin=Decimal("500"),
        instrument=instrument(),
        model=BrokerSimulationModel(margin_per_lot=Decimal("100")),
    )

    assert decision.status is FillStatus.FULL
    assert decision.filled_volume == Decimal("1")
    assert decision.margin_required == Decimal("100")
    assert decision.reason_codes == ()


def test_liquidity_and_margin_caps_produce_rounded_partial_fills() -> None:
    liquidity = simulate_broker_fill_decision(
        requested_volume=Decimal("1"),
        available_margin=Decimal("500"),
        instrument=instrument(),
        model=BrokerSimulationModel(margin_per_lot=Decimal("100"), liquidity_cap=Decimal("0.505")),
    )
    margin = simulate_broker_fill_decision(
        requested_volume=Decimal("1"),
        available_margin=Decimal("35"),
        instrument=instrument(),
        model=BrokerSimulationModel(margin_per_lot=Decimal("100")),
    )
    entry_cost_limited = simulate_broker_fill_decision(
        requested_volume=Decimal("1"),
        available_margin=Decimal("100"),
        instrument=instrument(),
        model=BrokerSimulationModel(margin_per_lot=Decimal("90")),
        commission_per_lot=Decimal("20"),
    )

    assert liquidity.status is FillStatus.PARTIAL
    assert liquidity.filled_volume == Decimal("0.50")
    assert liquidity.remaining_volume == Decimal("0.50")
    assert liquidity.reason_codes == (
        FillReason.LIQUIDITY_LIMIT,
        FillReason.VOLUME_ROUNDED,
    )
    assert margin.status is FillStatus.PARTIAL
    assert margin.filled_volume == Decimal("0.35")
    assert margin.margin_required == Decimal("35.00")
    assert margin.reason_codes == (FillReason.INSUFFICIENT_MARGIN,)
    assert entry_cost_limited.filled_volume == Decimal("0.90")
    assert entry_cost_limited.margin_required == Decimal("81.00")
    assert entry_cost_limited.reason_codes == (
        FillReason.INSUFFICIENT_MARGIN,
        FillReason.VOLUME_ROUNDED,
    )


def test_unfillable_or_disallowed_partial_order_is_rejected() -> None:
    below_minimum = simulate_broker_fill_decision(
        requested_volume=Decimal("0.005"),
        available_margin=Decimal("500"),
        instrument=instrument(),
        model=BrokerSimulationModel(),
    )
    disabled = simulate_broker_fill_decision(
        requested_volume=Decimal("1"),
        available_margin=Decimal("500"),
        instrument=instrument(),
        model=BrokerSimulationModel(liquidity_cap=Decimal("0.50"), allow_partial_fills=False),
    )

    assert below_minimum.status is FillStatus.REJECTED
    assert below_minimum.reason_codes == (FillReason.BELOW_MINIMUM_VOLUME,)
    assert disabled.status is FillStatus.REJECTED
    assert disabled.filled_volume == 0
    assert FillReason.PARTIAL_FILL_DISABLED in disabled.reason_codes


def test_swap_skips_weekends_and_applies_triple_weekday() -> None:
    model = BrokerSimulationModel(
        long_swap_cost_per_lot=Decimal("2"),
        short_swap_cost_per_lot=Decimal("3"),
        triple_swap_weekday=2,
    )

    one_week = calculate_swap_cost(
        side=Action.BUY,
        volume=Decimal("1"),
        opened_at=MONDAY,
        closed_at=MONDAY + timedelta(days=7),
        model=model,
    )
    same_day = calculate_swap_cost(
        side=Action.SELL,
        volume=Decimal("0.5"),
        opened_at=MONDAY,
        closed_at=MONDAY + timedelta(hours=1),
        model=model,
    )

    assert one_week == Decimal("14")
    assert same_day == 0


def test_broker_model_and_fill_inputs_fail_closed() -> None:
    with pytest.raises(ValueError, match="liquidity"):
        BrokerSimulationModel(liquidity_cap=Decimal("0"))
    with pytest.raises(ValueError, match="requested volume"):
        simulate_broker_fill_decision(
            requested_volume=Decimal("0"),
            available_margin=Decimal("100"),
            instrument=instrument(),
            model=BrokerSimulationModel(),
        )
    with pytest.raises(ValueError, match="BUY or SELL"):
        calculate_swap_cost(
            side=Action.HOLD,
            volume=Decimal("1"),
            opened_at=MONDAY,
            closed_at=MONDAY,
            model=BrokerSimulationModel(),
        )
