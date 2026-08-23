"""Deterministic local PAPER adapter; deliberately has no broker/network path."""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from quantora_trade.domain.enums import Action
from quantora_trade.domain.ports import ClockPort
from quantora_trade.execution.lifecycle import require_transition
from quantora_trade.execution.models import (
    Fill,
    OrderEvent,
    OrderStatus,
    PaperOrder,
    PaperOrderRequest,
    PaperQuote,
)


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for a different request payload."""


@dataclass(frozen=True, slots=True)
class PaperFillPolicy:
    slippage_points: int = 0
    commission_per_volume: Decimal = Decimal("0")
    max_quote_age: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        if self.slippage_points < 0:
            raise ValueError("slippage_points must be non-negative")
        if not self.commission_per_volume.is_finite() or self.commission_per_volume < 0:
            raise ValueError("commission_per_volume must be finite and non-negative")
        if self.max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be greater than zero")


def request_hash(request: PaperOrderRequest) -> str:
    payload = json.dumps(
        {
            "approved_intent_id": str(request.approved_intent_id),
            "expires_at": request.expires_at.isoformat(),
            "mode": request.mode.value,
            "broker_id": str(request.instrument.broker_id),
            "contract_multiplier": str(request.instrument.contract_multiplier),
            "instrument_id": str(request.instrument.instrument_id),
            "point": str(request.instrument.point),
            "quote_currency": request.instrument.quote_currency,
            "specification_hash": request.instrument.specification_hash,
            "side": request.side.value,
            "symbol": request.symbol,
            "volume": str(request.volume),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class DeterministicPaperAdapter:
    """In-memory PAPER execution with repeatable cost and liquidity rules."""

    def __init__(self, *, clock: ClockPort, policy: PaperFillPolicy) -> None:
        self._clock = clock
        self._policy = policy
        self._orders: dict[str, PaperOrder] = {}

    def submit(self, request: PaperOrderRequest, quote: PaperQuote) -> PaperOrder:
        digest = request_hash(request)
        replay = self._orders.get(request.idempotency_key)
        if replay is not None:
            if replay.request_hash != digest:
                raise IdempotencyConflict("idempotency key reused with a different request")
            return replay
        now = self._clock.now()
        order = PaperOrder(
            id=uuid5(NAMESPACE_URL, f"paper:{request.idempotency_key}:{digest}"),
            request_hash=digest,
            request=request,
            status=OrderStatus.CREATED,
            filled_volume=Decimal("0"),
            fills=(),
            events=(OrderEvent(1, OrderStatus.CREATED, now, "ORDER_CREATED"),),
        )
        if now >= request.expires_at:
            order = self._transition(order, OrderStatus.EXPIRED, "ORDER_EXPIRED")
        elif quote.symbol != request.symbol:
            order = self._transition(order, OrderStatus.REJECTED, "QUOTE_SYMBOL_MISMATCH")
        elif (quote_error := self._quote_error(quote, now)) is not None:
            order = self._transition(order, OrderStatus.REJECTED, quote_error)
        else:
            order = self._transition(order, OrderStatus.ACCEPTED, "ORDER_ACCEPTED")
            order = self._fill(order, quote)
        self._orders[request.idempotency_key] = order
        return order

    def add_liquidity(self, idempotency_key: str, quote: PaperQuote) -> PaperOrder:
        order = self._get(idempotency_key)
        if quote.symbol != order.request.symbol:
            raise ValueError("quote symbol does not match order")
        now = self._clock.now()
        if now >= order.request.expires_at:
            order = self._transition(order, OrderStatus.EXPIRED, "ORDER_EXPIRED")
        else:
            quote_error = self._quote_error(quote, now)
            if quote_error is not None:
                raise ValueError(quote_error)
            order = self._fill(order, quote)
        self._orders[idempotency_key] = order
        return order

    def cancel(self, idempotency_key: str) -> PaperOrder:
        order = self._get(idempotency_key)
        order = self._transition(order, OrderStatus.CANCEL_PENDING, "CANCEL_REQUESTED")
        order = self._transition(order, OrderStatus.CANCELLED, "ORDER_CANCELLED")
        self._orders[idempotency_key] = order
        return order

    def expire(self, idempotency_key: str) -> PaperOrder:
        order = self._get(idempotency_key)
        if self._clock.now() < order.request.expires_at:
            raise ValueError("order has not reached expires_at")
        order = self._transition(order, OrderStatus.EXPIRED, "ORDER_EXPIRED")
        self._orders[idempotency_key] = order
        return order

    def get(self, idempotency_key: str) -> PaperOrder:
        return self._get(idempotency_key)

    def restore(self, order: PaperOrder) -> None:
        """Hydrate one durable snapshot without replaying any lifecycle action."""

        key = order.request.idempotency_key
        existing = self._orders.get(key)
        if existing is not None and existing != order:
            raise IdempotencyConflict("in-memory state differs from durable state")
        self._orders[key] = order

    def reconcile(self, order: PaperOrder) -> None:
        """Replace volatile state with the durable snapshot of the same order."""

        key = order.request.idempotency_key
        existing = self._orders.get(key)
        if existing is not None and (
            existing.id != order.id
            or existing.request_hash != order.request_hash
            or existing.request != order.request
        ):
            raise IdempotencyConflict("durable state belongs to a different request")
        self._orders[key] = order

    def _get(self, key: str) -> PaperOrder:
        try:
            return self._orders[key]
        except KeyError as error:
            raise KeyError("paper order not found") from error

    def _transition(self, order: PaperOrder, target: OrderStatus, code: str) -> PaperOrder:
        require_transition(order.status, target)
        event = OrderEvent(len(order.events) + 1, target, self._clock.now(), code)
        return replace(order, status=target, events=(*order.events, event))

    def _fill(self, order: PaperOrder, quote: PaperQuote) -> PaperOrder:
        if order.status not in {OrderStatus.ACCEPTED, OrderStatus.PARTIAL}:
            raise ValueError("only accepted or partial orders can fill")
        volume = min(order.remaining_volume, quote.available_volume)
        if volume == 0:
            return order
        adjustment = order.request.point * self._policy.slippage_points
        price = (
            quote.ask + adjustment if order.request.side is Action.BUY else quote.bid - adjustment
        )
        if price <= 0:
            raise ValueError("slippage produced a non-positive fill price")
        fill = Fill(
            volume=volume,
            price=price,
            commission=volume * self._policy.commission_per_volume,
            filled_at=self._clock.now(),
        )
        filled_volume = order.filled_volume + volume
        target = (
            OrderStatus.FILLED if filled_volume == order.request.volume else OrderStatus.PARTIAL
        )
        transitioned = self._transition(
            order, target, "ORDER_FILLED" if target is OrderStatus.FILLED else "ORDER_PARTIAL_FILL"
        )
        return replace(
            transitioned,
            filled_volume=filled_volume,
            fills=(*order.fills, fill),
        )

    def _quote_error(self, quote: PaperQuote, now: datetime) -> str | None:
        """Return a stable reason code when a quote is non-causal or stale."""

        if quote.observed_at > now:
            return "QUOTE_FROM_FUTURE"
        if now - quote.observed_at > self._policy.max_quote_age:
            return "QUOTE_TOO_OLD"
        return None
