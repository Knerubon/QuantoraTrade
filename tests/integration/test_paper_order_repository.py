"""PostgreSQL integration tests for durable PAPER execution evidence."""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.execution.models import (
    Fill,
    InstrumentExecutionSnapshot,
    OrderEvent,
    OrderStatus,
    PaperOrder,
    PaperOrderRequest,
)
from quantora_trade.execution.paper import IdempotencyConflict
from quantora_trade.infrastructure.database.order_repository import (
    ConcurrentPaperOrderUpdate,
    PostgresPaperOrderRepository,
)

DATABASE_URL = os.getenv("QUANTORA_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("QUANTORA_DATABASE_URL is required for integration tests", allow_module_level=True)

engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(engine, expire_on_commit=False)
NOW = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    with SessionFactory() as session, session.begin():
        session.execute(
            text(
                "TRUNCATE quantora.paper_mark_events, quantora.paper_accounting_events, "
                "quantora.paper_positions, quantora.paper_accounts, quantora.paper_fills, "
                "quantora.paper_order_events, quantora.paper_orders RESTART IDENTITY"
            )
        )


def created_order(key: str = "paper-1") -> PaperOrder:
    request = PaperOrderRequest(
        approved_intent_id=uuid4(),
        idempotency_key=key,
        mode=TradingMode.PAPER,
        symbol="XAUUSD",
        side=Action.BUY,
        volume=Decimal("1"),
        instrument=InstrumentExecutionSnapshot(
            uuid4(), uuid4(), "a" * 64, "USD", Decimal("100"), Decimal("0.01")
        ),
        expires_at=NOW + timedelta(minutes=15),
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


def partial_order(value: PaperOrder) -> PaperOrder:
    return replace(
        value,
        status=OrderStatus.PARTIAL,
        filled_volume=Decimal("0.4"),
        fills=(Fill(Decimal("0.4"), Decimal("2400.1"), Decimal("0.2"), NOW),),
        events=(
            *value.events,
            OrderEvent(2, OrderStatus.ACCEPTED, NOW, "ORDER_ACCEPTED"),
            OrderEvent(3, OrderStatus.PARTIAL, NOW, "ORDER_PARTIAL_FILL"),
        ),
    )


def test_restart_readback_idempotent_replay_and_optimistic_update() -> None:
    repository = PostgresPaperOrderRepository(SessionFactory)
    initial = created_order()
    assert repository.persist(initial) == initial
    assert repository.persist(initial) == initial
    restarted = PostgresPaperOrderRepository(SessionFactory)
    assert restarted.get("paper-1") == initial

    partial = partial_order(initial)
    assert restarted.persist(partial, expected_sequence=1) == partial
    assert PostgresPaperOrderRepository(SessionFactory).get("paper-1") == partial
    filled = replace(
        partial,
        status=OrderStatus.FILLED,
        filled_volume=Decimal("1"),
        fills=(
            *partial.fills,
            Fill(Decimal("0.6"), Decimal("2400.2"), Decimal("0.3"), NOW),
        ),
        events=(
            *partial.events,
            OrderEvent(4, OrderStatus.FILLED, NOW, "ORDER_FILLED"),
        ),
    )
    with pytest.raises(ConcurrentPaperOrderUpdate, match="sequence changed"):
        restarted.persist(filled, expected_sequence=1)


def test_idempotency_key_cannot_be_rebound() -> None:
    repository = PostgresPaperOrderRepository(SessionFactory)
    initial = created_order()
    repository.persist(initial)
    collision = replace(initial, id=uuid4(), request_hash="b" * 64)
    with pytest.raises(IdempotencyConflict, match="different request"):
        repository.persist(collision)


def test_database_rejects_evidence_mutation_and_fill_overrun() -> None:
    value = partial_order(created_order())
    PostgresPaperOrderRepository(SessionFactory).persist(value)
    with (
        SessionFactory() as session,
        session.begin(),
        pytest.raises(DBAPIError, match="append-only"),
    ):
        session.execute(
            text("UPDATE quantora.paper_fills SET volume = 0.5 WHERE order_id = :id"),
            {"id": value.id},
        )
    with SessionFactory() as session, session.begin(), pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO quantora.paper_fills "
                "(order_id, sequence, volume, price, commission, filled_at) "
                "VALUES (:id, 2, 2, 2400, 0, :at)"
            ),
            {"id": value.id, "at": NOW},
        )


def test_database_rejects_illegal_and_terminal_order_transitions() -> None:
    repository = PostgresPaperOrderRepository(SessionFactory)
    initial = created_order()
    repository.persist(initial)
    with SessionFactory() as session, session.begin(), pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO quantora.paper_order_events "
                "(order_id, sequence, status, occurred_at, code) "
                "VALUES (:id, 2, 'filled', :at, 'ILLEGAL')"
            ),
            {"id": initial.id, "at": NOW},
        )
        session.execute(
            text("UPDATE quantora.paper_orders SET status = 'filled', version = 2 WHERE id = :id"),
            {"id": initial.id},
        )

    rejected = replace(
        initial,
        status=OrderStatus.REJECTED,
        events=(*initial.events, OrderEvent(2, OrderStatus.REJECTED, NOW, "REJECTED")),
    )
    repository.persist(rejected, expected_sequence=1)
    with SessionFactory() as session, session.begin(), pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO quantora.paper_order_events "
                "(order_id, sequence, status, occurred_at, code) "
                "VALUES (:id, 3, 'accepted', :at, 'ILLEGAL')"
            ),
            {"id": initial.id, "at": NOW},
        )
        session.execute(
            text(
                "UPDATE quantora.paper_orders SET status = 'accepted', version = 3 WHERE id = :id"
            ),
            {"id": initial.id},
        )
