"""PostgreSQL integration coverage for fill-derived PAPER accounting."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
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
from quantora_trade.infrastructure.database.accounting_repository import (
    PostgresPaperAccountingRepository,
)
from quantora_trade.infrastructure.database.market_data_models import BrokerModel, InstrumentModel
from quantora_trade.infrastructure.database.order_repository import PostgresPaperOrderRepository

DATABASE_URL = os.getenv("QUANTORA_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("QUANTORA_DATABASE_URL is required for integration tests", allow_module_level=True)

engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(engine, expire_on_commit=False)
NOW = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)
BROKER_ID = uuid4()
INSTRUMENT_ID = uuid4()


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    with SessionFactory() as session, session.begin():
        session.execute(
            text(
                "TRUNCATE quantora.paper_mark_events, quantora.paper_accounting_events, "
                "quantora.paper_positions, "
                "quantora.paper_accounts, quantora.paper_fills, quantora.paper_order_events, "
                "quantora.paper_orders RESTART IDENTITY"
            )
        )
        session.add(
            BrokerModel(
                id=BROKER_ID,
                code=f"ACCOUNTING-{BROKER_ID}",
                name="Accounting Test Broker",
                adapter_type="test",
                enabled=True,
                created_at=NOW,
            )
        )
        session.add(
            InstrumentModel(
                id=INSTRUMENT_ID,
                broker_id=BROKER_ID,
                symbol="XAUUSD",
                canonical_symbol="XAUUSD",
                asset_class="metal",
                quote_currency="USD",
                digits=2,
                point=Decimal("0.01"),
                pip_size=Decimal("0.01"),
                tick_size=Decimal("0.01"),
                tick_value=Decimal("1"),
                contract_size=Decimal("100"),
                spread_points=10,
                session_timezone="UTC",
                session_profile="24x5",
                volume_min=Decimal("0.01"),
                volume_max=Decimal("100"),
                volume_step=Decimal("0.01"),
                specification_hash="a" * 64,
                observed_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    yield
    with SessionFactory() as session, session.begin():
        session.execute(
            text("DELETE FROM quantora.instruments WHERE id = :id"), {"id": INSTRUMENT_ID}
        )
        session.execute(text("DELETE FROM quantora.brokers WHERE id = :id"), {"id": BROKER_ID})


def filled_order() -> PaperOrder:
    request = PaperOrderRequest(
        approved_intent_id=uuid4(),
        idempotency_key="accounting-fill-1",
        mode=TradingMode.PAPER,
        symbol="XAUUSD",
        side=Action.BUY,
        volume=Decimal("1"),
        instrument=InstrumentExecutionSnapshot(
            INSTRUMENT_ID, BROKER_ID, "a" * 64, "USD", Decimal("100"), Decimal("0.01")
        ),
        expires_at=NOW + timedelta(minutes=15),
    )
    return PaperOrder(
        id=uuid4(),
        request_hash="c" * 64,
        request=request,
        status=OrderStatus.FILLED,
        filled_volume=Decimal("1"),
        fills=(Fill(Decimal("1"), Decimal("2400"), Decimal("2"), NOW),),
        events=(
            OrderEvent(1, OrderStatus.CREATED, NOW, "ORDER_CREATED"),
            OrderEvent(2, OrderStatus.ACCEPTED, NOW, "ORDER_ACCEPTED"),
            OrderEvent(3, OrderStatus.FILLED, NOW, "ORDER_FILLED"),
        ),
    )


def test_fill_projection_is_idempotent_and_mark_updates_drawdown() -> None:
    order = filled_order()
    PostgresPaperOrderRepository(SessionFactory).persist(order)
    accounting = PostgresPaperAccountingRepository(SessionFactory)
    accounting.initialize("USD", Decimal("10000"), NOW)

    first = accounting.project_fill(
        order.id,
        1,
        recorded_at=NOW,
    )
    replay = accounting.project_fill(
        order.id,
        1,
        recorded_at=NOW,
    )
    marked = accounting.mark(INSTRUMENT_ID, Decimal("2390"), observed_at=NOW)

    assert first == replay
    assert first.fees == Decimal("2")
    assert marked.unrealized_pnl == Decimal("-1000")
    assert marked.drawdown == Decimal("1002")
    with SessionFactory() as session:
        assert session.scalar(text("SELECT count(*) FROM quantora.paper_accounting_events")) == 1
        assert session.scalar(text("SELECT count(*) FROM quantora.paper_mark_events")) == 1


def test_fill_projection_uses_execution_snapshot_after_specification_mutation() -> None:
    order = filled_order()
    PostgresPaperOrderRepository(SessionFactory).persist(order)
    accounting = PostgresPaperAccountingRepository(SessionFactory)
    accounting.initialize("USD", Decimal("10000"), NOW)
    with SessionFactory() as session, session.begin():
        session.execute(
            text(
                "UPDATE quantora.instruments SET contract_size = 999, "
                "quote_currency = 'EUR', specification_hash = :hash WHERE id = :id"
            ),
            {"id": INSTRUMENT_ID, "hash": "f" * 64},
        )

    projected = accounting.project_fill(order.id, 1, recorded_at=NOW)
    assert projected.fees == Decimal("2")
    with SessionFactory() as session:
        evidence = session.execute(
            text(
                "SELECT quote_currency, contract_multiplier, specification_hash "
                "FROM quantora.paper_accounting_events"
            )
        ).one()
        assert evidence == ("USD", Decimal("100"), "a" * 64)
