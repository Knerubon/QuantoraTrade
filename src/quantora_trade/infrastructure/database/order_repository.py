"""Transactional PostgreSQL repository for deterministic PAPER order state."""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.execution.lifecycle import require_transition
from quantora_trade.execution.models import (
    Fill,
    InstrumentExecutionSnapshot,
    OrderEvent,
    OrderStatus,
    PaperOrder,
    PaperOrderRequest,
)
from quantora_trade.execution.paper import IdempotencyConflict
from quantora_trade.infrastructure.database.order_models import (
    PaperFillModel,
    PaperOrderEventModel,
    PaperOrderModel,
)


class ConcurrentPaperOrderUpdate(RuntimeError):
    """The caller attempted to replace state based on a stale sequence."""


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class PostgresPaperOrderRepository:
    """Persist snapshots by appending evidence and atomically advancing current state."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get(self, idempotency_key: str) -> PaperOrder | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(PaperOrderModel).where(PaperOrderModel.idempotency_key == idempotency_key)
            )
            return None if row is None else self._order(session, row)

    def persist(self, order: PaperOrder, *, expected_sequence: int | None = None) -> PaperOrder:
        self._validate(order)
        try:
            with self._session_factory() as session, session.begin():
                row = session.scalar(
                    select(PaperOrderModel)
                    .where(PaperOrderModel.idempotency_key == order.request.idempotency_key)
                    .with_for_update()
                )
                if row is None:
                    if expected_sequence not in (None, 0):
                        raise ConcurrentPaperOrderUpdate(
                            "paper order does not exist at expected sequence"
                        )
                    row = self._new_row(order)
                    session.add(row)
                    session.flush()
                    self._append(session, order, event_start=0, fill_start=0)
                    return order

                current = self._order(session, row)
                self._require_identity(current, order)
                if current == order:
                    return current
                if expected_sequence is not None and expected_sequence != len(current.events):
                    raise ConcurrentPaperOrderUpdate("paper order sequence changed")
                if len(order.events) <= len(current.events):
                    raise ConcurrentPaperOrderUpdate("paper order snapshot is stale")
                if order.events[: len(current.events)] != current.events:
                    raise ValueError("persisted order events are immutable")
                if (
                    len(order.fills) < len(current.fills)
                    or order.fills[: len(current.fills)] != current.fills
                ):
                    raise ValueError("persisted fills are immutable")
                self._append(
                    session,
                    order,
                    event_start=len(current.events),
                    fill_start=len(current.fills),
                )
                row.status = order.status.value
                row.filled_volume = order.filled_volume
                row.version = len(order.events)
                session.flush()
                return order
        except IntegrityError as error:
            raise IdempotencyConflict(
                "paper order identity already belongs to another request"
            ) from error

    save = persist

    @staticmethod
    def _validate(order: PaperOrder) -> None:
        if order.request.mode is not TradingMode.PAPER:
            raise ValueError("only PAPER orders may be persisted")
        if re.fullmatch(r"[0-9a-f]{64}", order.request_hash) is None:
            raise ValueError("request_hash must be a SHA-256 hex digest")
        if not order.events or tuple(event.sequence for event in order.events) != tuple(
            range(1, len(order.events) + 1)
        ):
            raise ValueError("events must have contiguous sequence starting at one")
        if order.events[0].status is not OrderStatus.CREATED:
            raise ValueError("paper order must begin in created state")
        total = sum((fill.volume for fill in order.fills), Decimal("0"))
        if total != order.filled_volume or total > order.request.volume:
            raise ValueError("filled volume must equal fills and not exceed requested volume")
        if order.events[-1].status is not order.status:
            raise ValueError("latest event status must equal order status")
        for current, target in zip(order.events, order.events[1:], strict=False):
            require_transition(current.status, target.status)

    @staticmethod
    def _require_identity(current: PaperOrder, candidate: PaperOrder) -> None:
        if (
            current.id != candidate.id
            or current.request_hash != candidate.request_hash
            or current.request != candidate.request
        ):
            raise IdempotencyConflict("idempotency key reused with a different request")

    @staticmethod
    def _new_row(order: PaperOrder) -> PaperOrderModel:
        request = order.request
        return PaperOrderModel(
            id=order.id,
            idempotency_key=request.idempotency_key,
            request_hash=order.request_hash,
            approved_intent_id=request.approved_intent_id,
            instrument_id=request.instrument.instrument_id,
            broker_id=request.instrument.broker_id,
            specification_hash=request.instrument.specification_hash,
            quote_currency=request.instrument.quote_currency,
            contract_multiplier=request.instrument.contract_multiplier,
            mode=request.mode.value,
            symbol=request.symbol,
            side=request.side.value,
            requested_volume=request.volume,
            point=request.instrument.point,
            expires_at=request.expires_at,
            status=order.status.value,
            filled_volume=order.filled_volume,
            version=len(order.events),
        )

    @staticmethod
    def _append(session: Session, order: PaperOrder, *, event_start: int, fill_start: int) -> None:
        session.add_all(
            PaperOrderEventModel(
                order_id=order.id,
                sequence=event.sequence,
                status=event.status.value,
                occurred_at=event.occurred_at,
                code=event.code,
            )
            for event in order.events[event_start:]
        )
        session.add_all(
            PaperFillModel(
                order_id=order.id,
                sequence=index,
                volume=fill.volume,
                price=fill.price,
                commission=fill.commission,
                filled_at=fill.filled_at,
            )
            for index, fill in enumerate(order.fills[fill_start:], start=fill_start + 1)
        )

    @staticmethod
    def _order(session: Session, row: PaperOrderModel) -> PaperOrder:
        event_rows = session.scalars(
            select(PaperOrderEventModel)
            .where(PaperOrderEventModel.order_id == row.id)
            .order_by(PaperOrderEventModel.sequence)
        )
        fill_rows = session.scalars(
            select(PaperFillModel)
            .where(PaperFillModel.order_id == row.id)
            .order_by(PaperFillModel.sequence)
        )
        request = PaperOrderRequest(
            approved_intent_id=row.approved_intent_id,
            idempotency_key=row.idempotency_key,
            mode=TradingMode(row.mode),
            symbol=row.symbol,
            side=Action(row.side),
            volume=row.requested_volume,
            instrument=InstrumentExecutionSnapshot(
                instrument_id=row.instrument_id,
                broker_id=row.broker_id,
                specification_hash=row.specification_hash,
                quote_currency=row.quote_currency,
                contract_multiplier=row.contract_multiplier,
                point=row.point,
            ),
            expires_at=_utc(row.expires_at),
        )
        return PaperOrder(
            id=row.id,
            request_hash=row.request_hash,
            request=request,
            status=OrderStatus(row.status),
            filled_volume=row.filled_volume,
            fills=tuple(
                Fill(item.volume, item.price, item.commission, _utc(item.filled_at))
                for item in fill_rows
            ),
            events=tuple(
                OrderEvent(
                    item.sequence, OrderStatus(item.status), _utc(item.occurred_at), item.code
                )
                for item in event_rows
            ),
        )


__all__ = ["ConcurrentPaperOrderUpdate", "PostgresPaperOrderRepository"]
