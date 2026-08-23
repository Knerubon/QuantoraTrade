"""Durable facade for the deterministic PAPER adapter."""

from collections.abc import Callable
from typing import Protocol

from quantora_trade.execution.models import PaperOrder, PaperOrderRequest, PaperQuote
from quantora_trade.execution.paper import (
    DeterministicPaperAdapter,
    IdempotencyConflict,
    request_hash,
)


class PaperOrderRepository(Protocol):
    def get(self, idempotency_key: str) -> PaperOrder | None: ...

    def persist(self, order: PaperOrder, *, expected_sequence: int | None = None) -> PaperOrder: ...


class PaperOrderReconciliationRequired(RuntimeError):
    """A concurrent mutation could not be resolved within the bounded retry."""


class DurablePaperAdapter:
    """Persist every PAPER transition and recover snapshots after restart.

    The wrapped adapter has no external side effect. Therefore a crash before the
    repository commit is safely replayable, while the submission journal remains
    the owner of submission fencing at the service boundary.
    """

    def __init__(
        self, *, adapter: DeterministicPaperAdapter, repository: PaperOrderRepository
    ) -> None:
        self._adapter = adapter
        self._repository = repository

    def submit(self, request: PaperOrderRequest, quote: PaperQuote) -> PaperOrder:
        current = self._repository.get(request.idempotency_key)
        if current is not None:
            if current.request_hash != request_hash(request) or current.request != request:
                raise IdempotencyConflict("idempotency key reused with a different request")
            self._adapter.restore(current)
            return current
        candidate = self._adapter.submit(request, quote)
        try:
            return self._repository.persist(candidate, expected_sequence=0)
        except IdempotencyConflict:
            replay = self._repository.get(request.idempotency_key)
            if replay is None or replay.request_hash != candidate.request_hash:
                raise
            self._adapter.restore(replay)
            return replay

    def add_liquidity(self, idempotency_key: str, quote: PaperQuote) -> PaperOrder:
        return self._mutate(
            idempotency_key, lambda: self._adapter.add_liquidity(idempotency_key, quote)
        )

    def cancel(self, idempotency_key: str) -> PaperOrder:
        return self._mutate(idempotency_key, lambda: self._adapter.cancel(idempotency_key))

    def expire(self, idempotency_key: str) -> PaperOrder:
        return self._mutate(idempotency_key, lambda: self._adapter.expire(idempotency_key))

    def get(self, idempotency_key: str) -> PaperOrder:
        order = self._repository.get(idempotency_key)
        if order is None:
            raise KeyError("paper order not found")
        self._adapter.restore(order)
        return order

    def _mutate(self, key: str, action: Callable[[], PaperOrder]) -> PaperOrder:
        # A single reload/retry resolves the common optimistic-concurrency race
        # without turning this local adapter into an unbounded retry loop.
        for attempt in range(2):
            current = self._repository.get(key)
            if current is None:
                raise KeyError("paper order not found")
            self._adapter.reconcile(current)
            try:
                candidate = action()
                return self._repository.persist(candidate, expected_sequence=len(current.events))
            except RuntimeError as error:
                if attempt == 0:
                    continue
                raise PaperOrderReconciliationRequired(
                    "concurrent PAPER mutation requires explicit reconciliation"
                ) from error
        raise AssertionError("bounded mutation loop exhausted")


__all__ = [
    "DurablePaperAdapter",
    "PaperOrderReconciliationRequired",
    "PaperOrderRepository",
]
