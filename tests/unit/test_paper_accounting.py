"""Deterministic PAPER accounting tests."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from quantora_trade.accounting.models import AccountingFill, AccountSnapshot, PositionSnapshot
from quantora_trade.accounting.service import apply_fill, mark_position, update_account
from quantora_trade.infrastructure.database.accounting_models import (
    PaperAccountingEventModel,
    PaperMarkEventModel,
    PaperPositionModel,
)

NOW = datetime(2026, 8, 23, tzinfo=UTC)
INSTRUMENT_ID = uuid4()
BROKER_ID = uuid4()


def fill(side: str, quantity: str, price: str, *, sequence: int = 1) -> AccountingFill:
    return AccountingFill(
        order_id=uuid4(),
        sequence=sequence,
        instrument_id=INSTRUMENT_ID,
        broker_id=BROKER_ID,
        specification_hash="a" * 64,
        symbol="XAUUSD",
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        commission=Decimal("2"),
        filled_at=NOW,
        quote_currency="USD",
        contract_multiplier=Decimal("100"),
    )


def account() -> AccountSnapshot:
    return AccountSnapshot(
        currency="USD",
        initial_balance=Decimal("10000"),
        cash_balance=Decimal("10000"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        fees=Decimal("0"),
        equity=Decimal("10000"),
        equity_peak=Decimal("10000"),
        drawdown=Decimal("0"),
        drawdown_pct=Decimal("0"),
    )


def test_fill_reduction_and_reversal_use_average_cost_without_hiding_fees() -> None:
    position, realized = apply_fill(None, fill("buy", "2", "100"))
    position, realized = apply_fill(position, fill("sell", "3", "110", sequence=2))

    assert realized == Decimal("2000")
    assert position.net_quantity == Decimal("-1")
    assert position.average_price == Decimal("110")
    assert position.fees == Decimal("4")


def test_marks_update_unrealized_equity_peak_and_drawdown() -> None:
    position, _ = apply_fill(None, fill("buy", "1", "100"))
    winning = mark_position(position, Decimal("110"))
    peak = update_account(
        account(), (winning,), realized_delta=Decimal("0"), fee_delta=Decimal("2")
    )
    losing = mark_position(position, Decimal("90"))
    drawn = update_account(peak, (losing,), realized_delta=Decimal("0"), fee_delta=Decimal("0"))

    assert peak.equity == Decimal("10998")
    assert peak.equity_peak == Decimal("10998")
    assert drawn.equity == Decimal("8998")
    assert drawn.equity_peak == Decimal("10998")
    assert drawn.drawdown == Decimal("2000")
    assert drawn.drawdown_pct == Decimal("2000") / Decimal("10998")


def test_flat_position_and_invalid_marks_fail_closed() -> None:
    position, _ = apply_fill(None, fill("buy", "1", "100"))
    flat, realized = apply_fill(position, fill("sell", "1", "90", sequence=2))

    assert flat.net_quantity == 0
    assert flat.average_price == 0
    assert realized == Decimal("-1000")
    with pytest.raises(ValueError, match="positive"):
        mark_position(position, Decimal("0"))


def test_accounting_event_is_uniquely_tied_to_fill_evidence() -> None:
    table = PaperAccountingEventModel.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    foreign_columns = {
        tuple(element.parent.name for element in constraint.elements)
        for constraint in table.foreign_key_constraints
    }

    assert ("order_id", "fill_sequence") in unique_columns
    assert ("order_id", "fill_sequence") in foreign_columns


def test_account_rejects_cross_currency_summation_without_fx_conversion() -> None:
    foreign_position = PositionSnapshot(
        instrument_id=uuid4(),
        broker_id=uuid4(),
        specification_hash="b" * 64,
        symbol="EURGBP",
        quote_currency="GBP",
        net_quantity=Decimal("1"),
        average_price=Decimal("1"),
        mark_price=Decimal("1.1"),
        contract_multiplier=Decimal("100000"),
        unrealized_pnl=Decimal("10000"),
    )

    with pytest.raises(ValueError, match="FX conversion unavailable"):
        update_account(
            account(), (foreign_position,), realized_delta=Decimal("0"), fee_delta=Decimal("0")
        )


def test_mark_event_has_an_immutable_observation_key() -> None:
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in PaperMarkEventModel.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("instrument_id", "observed_at") in unique_columns


def test_same_symbol_from_two_brokers_has_distinct_position_identity() -> None:
    first_fill = fill("buy", "1", "100")
    second_fill = first_fill.model_copy(
        update={
            "order_id": uuid4(),
            "instrument_id": uuid4(),
            "broker_id": uuid4(),
            "specification_hash": "c" * 64,
        }
    )

    first, _ = apply_fill(None, first_fill)
    second, _ = apply_fill(None, second_fill)

    assert first.symbol == second.symbol == "XAUUSD"
    assert first.instrument_id != second.instrument_id
    assert PaperPositionModel.__table__.primary_key.columns.keys() == ["instrument_id"]
