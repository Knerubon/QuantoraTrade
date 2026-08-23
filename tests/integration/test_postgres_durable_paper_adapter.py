"""PostgreSQL restart proof for the durable deterministic PAPER adapter."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.execution import (
    DeterministicPaperAdapter,
    DurablePaperAdapter,
    InstrumentExecutionSnapshot,
    OrderStatus,
    PaperFillPolicy,
    PaperOrderRequest,
    PaperQuote,
)
from quantora_trade.infrastructure.database.order_repository import (
    PostgresPaperOrderRepository,
)

DATABASE_URL = os.getenv("QUANTORA_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("QUANTORA_DATABASE_URL is required for integration tests", allow_module_level=True)

engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(engine, expire_on_commit=False)
NOW = datetime(2026, 8, 23, 11, tzinfo=UTC)


@dataclass
class Clock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


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


def system(clock: Clock) -> DurablePaperAdapter:
    return DurablePaperAdapter(
        adapter=DeterministicPaperAdapter(clock=clock, policy=PaperFillPolicy()),
        repository=PostgresPaperOrderRepository(SessionFactory),
    )


def test_submit_restart_fill_and_replay_are_durable() -> None:
    clock = Clock()
    request = PaperOrderRequest(
        uuid4(),
        "pg-durable-paper",
        TradingMode.PAPER,
        "XAUUSD",
        Action.BUY,
        Decimal("1"),
        InstrumentExecutionSnapshot(
            uuid4(), uuid4(), "a" * 64, "USD", Decimal("100"), Decimal("0.01")
        ),
        NOW + timedelta(minutes=10),
    )
    partial_quote = PaperQuote("XAUUSD", Decimal("2500"), Decimal("2500.2"), Decimal("0.4"), NOW)
    partial = system(clock).submit(request, partial_quote)
    assert partial.status is OrderStatus.PARTIAL

    fill_quote = PaperQuote("XAUUSD", Decimal("2500.1"), Decimal("2500.3"), Decimal("0.6"), NOW)
    filled = system(clock).add_liquidity(request.idempotency_key, fill_quote)
    assert filled.status is OrderStatus.FILLED
    assert sum(fill.volume for fill in filled.fills) == request.volume

    restarted = system(clock)
    assert restarted.get(request.idempotency_key) == filled
    assert restarted.submit(request, partial_quote) == filled
