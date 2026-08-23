"""Restart and persistence proofs for durable PAPER execution."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.execution import (
    DeterministicPaperAdapter,
    DurablePaperAdapter,
    IdempotencyConflict,
    InstrumentExecutionSnapshot,
    OrderStatus,
    PaperFillPolicy,
    PaperOrder,
    PaperOrderReconciliationRequired,
    PaperOrderRequest,
    PaperQuote,
)

NOW = datetime(2026, 8, 23, 9, tzinfo=UTC)


@dataclass
class Clock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class Repository:
    def __init__(self) -> None:
        self.orders: dict[str, PaperOrder] = {}
        self.expected: list[int | None] = []

    def get(self, key: str) -> PaperOrder | None:
        return self.orders.get(key)

    def persist(self, order: PaperOrder, *, expected_sequence: int | None = None) -> PaperOrder:
        current = self.orders.get(order.request.idempotency_key)
        if current is not None and current.request_hash != order.request_hash:
            raise IdempotencyConflict("collision")
        if current is not None and expected_sequence != len(current.events):
            raise RuntimeError("stale")
        self.expected.append(expected_sequence)
        self.orders[order.request.idempotency_key] = order
        return order


def request(key: str = "paper-durable") -> PaperOrderRequest:
    return PaperOrderRequest(
        approved_intent_id=uuid4(),
        idempotency_key=key,
        mode=TradingMode.PAPER,
        symbol="XAUUSD",
        side=Action.BUY,
        volume=Decimal("1"),
        instrument=InstrumentExecutionSnapshot(
            uuid4(), uuid4(), "a" * 64, "USD", Decimal("100"), Decimal("0.01")
        ),
        expires_at=NOW + timedelta(minutes=10),
    )


def quote(volume: str) -> PaperQuote:
    return PaperQuote("XAUUSD", Decimal("2500"), Decimal("2500.2"), Decimal(volume), NOW)


def adapter(clock: Clock, repository: Repository) -> DurablePaperAdapter:
    return DurablePaperAdapter(
        adapter=DeterministicPaperAdapter(clock=clock, policy=PaperFillPolicy()),
        repository=repository,
    )


def test_submit_partial_restart_add_liquidity_and_cancel_are_durable() -> None:
    clock = Clock()
    repository = Repository()
    first = adapter(clock, repository)
    partial = first.submit(request(), quote("0.4"))
    assert partial.status is OrderStatus.PARTIAL
    assert repository.expected == [0]

    restarted = adapter(clock, repository)
    filled = restarted.add_liquidity("paper-durable", quote("0.6"))
    assert filled.status is OrderStatus.FILLED
    assert repository.expected == [0, len(partial.events)]
    assert adapter(clock, repository).get("paper-durable") == filled

    pending = adapter(clock, repository).submit(request("cancel-me"), quote("0"))
    cancelled = adapter(clock, repository).cancel("cancel-me")
    assert pending.status is OrderStatus.ACCEPTED
    assert cancelled.status is OrderStatus.CANCELLED


def test_restart_submit_replays_without_mutation_and_rejects_rebinding() -> None:
    clock = Clock()
    repository = Repository()
    original_request = request()
    original = adapter(clock, repository).submit(original_request, quote("1"))
    writes = list(repository.expected)
    assert adapter(clock, repository).submit(original_request, quote("0")) == original
    assert repository.expected == writes
    collision = PaperOrderRequest(
        approved_intent_id=original_request.approved_intent_id,
        idempotency_key=original_request.idempotency_key,
        mode=TradingMode.PAPER,
        symbol="XAUUSD",
        side=Action.BUY,
        volume=Decimal("2"),
        instrument=original_request.instrument,
        expires_at=original_request.expires_at,
    )
    with pytest.raises(IdempotencyConflict):
        adapter(clock, repository).submit(collision, quote("1"))


def test_expiry_is_restored_and_persisted_after_restart() -> None:
    clock = Clock()
    repository = Repository()
    accepted = adapter(clock, repository).submit(request(), quote("0"))
    clock.value = accepted.request.expires_at
    expired = adapter(clock, repository).expire("paper-durable")
    assert expired.status is OrderStatus.EXPIRED
    assert repository.expected[-1] == len(accepted.events)


def test_concurrent_mutation_reloads_once_then_succeeds() -> None:
    clock = Clock()
    repository = Repository()
    durable = adapter(clock, repository)
    durable.submit(request(), quote("0"))
    original_persist = repository.persist
    attempts = 0

    def race(order: PaperOrder, *, expected_sequence: int | None = None) -> PaperOrder:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("optimistic race")
        return original_persist(order, expected_sequence=expected_sequence)

    repository.persist = race  # type: ignore[method-assign]
    updated = durable.add_liquidity("paper-durable", quote("1"))

    assert updated.status is OrderStatus.FILLED
    assert attempts == 2


def test_repeated_concurrent_mutation_requires_explicit_reconciliation() -> None:
    clock = Clock()
    repository = Repository()
    durable = adapter(clock, repository)
    durable.submit(request(), quote("0"))

    def race(order: PaperOrder, *, expected_sequence: int | None = None) -> PaperOrder:
        raise RuntimeError("optimistic race")

    repository.persist = race  # type: ignore[method-assign]
    with pytest.raises(PaperOrderReconciliationRequired, match="explicit reconciliation"):
        durable.add_liquidity("paper-durable", quote("1"))


def test_submit_race_replays_matching_winner_and_rejects_missing_evidence() -> None:
    clock = Clock()
    repository = Repository()
    durable = adapter(clock, repository)
    original_persist = repository.persist
    winner: PaperOrder | None = None

    def matching_race(order: PaperOrder, *, expected_sequence: int | None = None) -> PaperOrder:
        nonlocal winner
        winner = order
        repository.orders[order.request.idempotency_key] = order
        raise IdempotencyConflict("concurrent insert")

    repository.persist = matching_race  # type: ignore[method-assign]
    assert durable.submit(request(), quote("1")) == winner

    repository.orders.clear()

    def missing_race(order: PaperOrder, *, expected_sequence: int | None = None) -> PaperOrder:
        raise IdempotencyConflict("concurrent insert without evidence")

    repository.persist = missing_race  # type: ignore[method-assign]
    with pytest.raises(IdempotencyConflict, match="without evidence"):
        durable.submit(request("missing"), quote("1"))
    repository.persist = original_persist  # type: ignore[method-assign]


def test_missing_order_fails_closed_for_get_and_mutation() -> None:
    durable = adapter(Clock(), Repository())
    with pytest.raises(KeyError, match="paper order not found"):
        durable.get("missing")
    with pytest.raises(KeyError, match="paper order not found"):
        durable.cancel("missing")
