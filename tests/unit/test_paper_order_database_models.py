from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.execution.models import (
    InstrumentExecutionSnapshot,
    OrderEvent,
    OrderStatus,
    PaperOrder,
    PaperOrderRequest,
)
from quantora_trade.infrastructure.database.order_models import (
    PaperFillModel,
    PaperOrderEventModel,
    PaperOrderModel,
)
from quantora_trade.infrastructure.database.order_repository import PostgresPaperOrderRepository

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def order() -> PaperOrder:
    request = PaperOrderRequest(
        approved_intent_id=uuid4(),
        idempotency_key="paper-key",
        mode=TradingMode.PAPER,
        symbol="XAUUSD",
        side=Action.BUY,
        volume=Decimal("1"),
        instrument=InstrumentExecutionSnapshot(
            uuid4(), uuid4(), "b" * 64, "USD", Decimal("100"), Decimal("0.01")
        ),
        expires_at=NOW + timedelta(minutes=5),
    )
    return PaperOrder(
        id=uuid4(),
        request_hash="a" * 64,
        request=request,
        status=OrderStatus.CREATED,
        filled_volume=Decimal("0"),
        fills=(),
        events=(OrderEvent(1, OrderStatus.CREATED, NOW, "ORDER_CREATED"),),
    )


def test_models_compile_with_paper_and_positive_constraints() -> None:
    ddl = " ".join(
        str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        for model in (PaperOrderModel, PaperOrderEventModel, PaperFillModel)
    )
    assert "mode = 'paper'" in ddl
    assert "filled_volume <= requested_volume" in ddl
    assert "volume > 0 AND price > 0 AND commission >= 0" in ddl
    assert "request_hash ~ '^[0-9a-f]{64}$'" in ddl
    assert "status IN ('created','accepted','partial'" in ddl


def test_repository_rejects_non_contiguous_events_and_fill_mismatch() -> None:
    value = order()
    invalid_sequence = PaperOrder(
        id=value.id,
        request_hash=value.request_hash,
        request=value.request,
        status=value.status,
        filled_volume=value.filled_volume,
        fills=value.fills,
        events=(OrderEvent(2, OrderStatus.CREATED, NOW, "ORDER_CREATED"),),
    )
    with pytest.raises(ValueError, match="contiguous"):
        PostgresPaperOrderRepository._validate(invalid_sequence)

    invalid_volume = PaperOrder(
        id=value.id,
        request_hash=value.request_hash,
        request=value.request,
        status=value.status,
        filled_volume=Decimal("0.5"),
        fills=(),
        events=value.events,
    )
    with pytest.raises(ValueError, match="filled volume"):
        PostgresPaperOrderRepository._validate(invalid_volume)


def test_repository_rejects_non_hex_hash_and_illegal_lifecycle() -> None:
    value = order()
    with pytest.raises(ValueError, match="SHA-256"):
        PostgresPaperOrderRepository._validate(replace(value, request_hash="z" * 64))
    invalid = replace(
        value,
        status=OrderStatus.FILLED,
        events=(*value.events, OrderEvent(2, OrderStatus.FILLED, NOW, "ORDER_FILLED")),
    )
    with pytest.raises(ValueError, match="invalid order transition"):
        PostgresPaperOrderRepository._validate(invalid)
