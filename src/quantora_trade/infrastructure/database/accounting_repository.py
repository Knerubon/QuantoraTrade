"""Transactional, idempotent accounting projection of immutable PAPER fills."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quantora_trade.accounting.models import (
    AccountingFill,
    AccountSnapshot,
    PositionSnapshot,
)
from quantora_trade.accounting.service import apply_fill as calculate_fill
from quantora_trade.accounting.service import mark_position, update_account
from quantora_trade.infrastructure.database.accounting_models import (
    PaperAccountingEventModel,
    PaperAccountModel,
    PaperMarkEventModel,
    PaperPositionModel,
)
from quantora_trade.infrastructure.database.order_models import PaperFillModel, PaperOrderModel


class PostgresPaperAccountingRepository:
    """Maintain current projections while preserving an append-only fill ledger."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def initialize(self, currency: str, initial_balance: Decimal, now: datetime) -> AccountSnapshot:
        if len(currency) != 3 or currency != currency.upper() or initial_balance <= 0:
            raise ValueError("currency and positive initial balance are required")
        with self._session_factory() as session, session.begin():
            session.execute(
                insert(PaperAccountModel)
                .values(
                    currency=currency,
                    initial_balance=initial_balance,
                    cash_balance=initial_balance,
                    realized_pnl=0,
                    unrealized_pnl=0,
                    fees=0,
                    equity=initial_balance,
                    equity_peak=initial_balance,
                    drawdown=0,
                    drawdown_pct=0,
                    updated_at=_utc(now),
                    version=0,
                )
                .on_conflict_do_nothing(index_elements=[PaperAccountModel.currency])
            )
            row = session.scalar(
                select(PaperAccountModel)
                .where(PaperAccountModel.currency == currency)
                .with_for_update()
            )
            if row is None:
                raise RuntimeError("paper account initialization did not produce a row")
            if row.initial_balance != initial_balance:
                raise ValueError("paper account is already initialized with another balance")
            return _account(row)

    def project_fill(
        self,
        order_id: UUID,
        fill_sequence: int,
        *,
        recorded_at: datetime,
    ) -> AccountSnapshot:
        """Project one database fill; duplicate evidence is a successful no-op.

        Price, quantity, side, fees, and event time are loaded from the immutable
        execution tables, rather than accepted from an external caller.
        """

        with self._session_factory() as session, session.begin():
            source = session.execute(
                select(PaperFillModel, PaperOrderModel)
                .join(PaperOrderModel, PaperOrderModel.id == PaperFillModel.order_id)
                .where(
                    PaperFillModel.order_id == order_id,
                    PaperFillModel.sequence == fill_sequence,
                )
            ).one_or_none()
            if source is None:
                raise ValueError("immutable PAPER fill evidence was not found")
            fill_row, order_row = source
            if order_row.side not in ("buy", "sell"):
                raise ValueError("persisted PAPER order side is invalid")
            fill = AccountingFill(
                order_id=order_id,
                sequence=fill_sequence,
                instrument_id=order_row.instrument_id,
                broker_id=order_row.broker_id,
                specification_hash=order_row.specification_hash,
                symbol=order_row.symbol,
                side=cast(Literal["buy", "sell"], order_row.side),
                quantity=fill_row.volume,
                price=fill_row.price,
                commission=fill_row.commission,
                filled_at=_utc(fill_row.filled_at),
                quote_currency=order_row.quote_currency,
                contract_multiplier=order_row.contract_multiplier,
            )
            account = session.scalar(
                select(PaperAccountModel)
                .where(PaperAccountModel.currency == fill.quote_currency)
                .with_for_update()
            )
            if account is None:
                raise ValueError("paper account must be initialized before applying fills")
            # The account row serializes all currency-level totals. Recheck the
            # evidence only after obtaining that lock so concurrent replay is a no-op.
            existing = session.scalar(
                select(PaperAccountingEventModel).where(
                    PaperAccountingEventModel.order_id == fill.order_id,
                    PaperAccountingEventModel.fill_sequence == fill.sequence,
                )
            )
            if existing is not None:
                _require_same_fill(existing, fill)
                return _account(account)

            position_row = session.scalar(
                select(PaperPositionModel)
                .where(PaperPositionModel.instrument_id == fill.instrument_id)
                .with_for_update()
            )
            if position_row is not None and _utc(fill.filled_at) < _utc(position_row.last_event_at):
                raise ValueError("PAPER fills must be projected in event-time order")
            current_position = None if position_row is None else _position(position_row)
            next_position, realized_delta = calculate_fill(current_position, fill)
            other_positions = tuple(
                _position(row)
                for row in session.scalars(
                    select(PaperPositionModel).where(
                        PaperPositionModel.instrument_id != fill.instrument_id,
                        PaperPositionModel.quote_currency == fill.quote_currency,
                    )
                )
            )
            next_account = update_account(
                _account(account),
                (*other_positions, next_position),
                realized_delta=realized_delta,
                fee_delta=fill.commission,
            )
            now = _utc(recorded_at)
            if position_row is None:
                position_row = PaperPositionModel(
                    instrument_id=fill.instrument_id,
                    broker_id=fill.broker_id,
                    specification_hash=fill.specification_hash,
                    symbol=fill.symbol,
                )
                session.add(position_row)
            _set_position(position_row, next_position, now, _utc(fill.filled_at))
            _set_account(account, next_account, now)
            session.add(
                PaperAccountingEventModel(
                    order_id=fill.order_id,
                    fill_sequence=fill.sequence,
                    instrument_id=fill.instrument_id,
                    broker_id=fill.broker_id,
                    specification_hash=fill.specification_hash,
                    symbol=fill.symbol,
                    side=fill.side,
                    quantity=fill.quantity,
                    price=fill.price,
                    commission=fill.commission,
                    filled_at=_utc(fill.filled_at),
                    quote_currency=fill.quote_currency,
                    contract_multiplier=fill.contract_multiplier,
                    realized_delta=realized_delta,
                    post_net_quantity=next_position.net_quantity,
                    post_average_price=next_position.average_price,
                    post_cash_balance=next_account.cash_balance,
                    post_equity=next_account.equity,
                    post_equity_peak=next_account.equity_peak,
                    post_drawdown=next_account.drawdown,
                    recorded_at=now,
                )
            )
            session.flush()
            return next_account

    def mark(
        self,
        instrument_id: UUID,
        price: Decimal,
        *,
        observed_at: datetime,
        recorded_at: datetime | None = None,
    ) -> AccountSnapshot:
        """Persist mark-to-market P&L without manufacturing execution evidence."""

        with self._session_factory() as session, session.begin():
            position_snapshot = session.scalar(
                select(PaperPositionModel).where(PaperPositionModel.instrument_id == instrument_id)
            )
            if position_snapshot is None:
                raise ValueError("an open PAPER position is required for marking")
            account = session.scalar(
                select(PaperAccountModel)
                .where(PaperAccountModel.currency == position_snapshot.quote_currency)
                .with_for_update()
            )
            if account is None:
                raise ValueError("PAPER account projection is unavailable")
            # Preserve the same currency -> symbol lock order used by fill projection.
            position_row = session.scalar(
                select(PaperPositionModel)
                .where(PaperPositionModel.instrument_id == instrument_id)
                .with_for_update()
            )
            if position_row is None or position_row.net_quantity == 0:
                raise ValueError("an open PAPER position is required for marking")
            existing_mark = session.scalar(
                select(PaperMarkEventModel).where(
                    PaperMarkEventModel.instrument_id == instrument_id,
                    PaperMarkEventModel.observed_at == _utc(observed_at),
                )
            )
            if existing_mark is not None:
                if existing_mark.price != price:
                    raise ValueError("mark evidence key was reused with another price")
                return _account(account)
            if _utc(observed_at) < _utc(position_row.last_event_at):
                raise ValueError("mark observation cannot move backward")
            prior_mark_price = position_row.mark_price
            marked = mark_position(_position(position_row), price)
            other_positions = tuple(
                _position(row)
                for row in session.scalars(
                    select(PaperPositionModel).where(
                        PaperPositionModel.instrument_id != instrument_id,
                        PaperPositionModel.quote_currency == position_row.quote_currency,
                    )
                )
            )
            next_account = update_account(
                _account(account),
                (*other_positions, marked),
                realized_delta=Decimal("0"),
                fee_delta=Decimal("0"),
            )
            observed = _utc(observed_at)
            recorded = _utc(recorded_at or observed_at)
            _set_position(position_row, marked, recorded, observed)
            _set_account(account, next_account, recorded)
            session.add(
                PaperMarkEventModel(
                    instrument_id=position_row.instrument_id,
                    broker_id=position_row.broker_id,
                    specification_hash=position_row.specification_hash,
                    symbol=position_row.symbol,
                    quote_currency=position_row.quote_currency,
                    price=price,
                    observed_at=observed,
                    prior_mark_price=prior_mark_price,
                    post_unrealized_pnl=marked.unrealized_pnl,
                    post_equity=next_account.equity,
                    post_equity_peak=next_account.equity_peak,
                    post_drawdown=next_account.drawdown,
                    recorded_at=recorded,
                )
            )
            session.flush()
            return next_account


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _account(row: PaperAccountModel) -> AccountSnapshot:
    return AccountSnapshot(
        currency=row.currency,
        initial_balance=row.initial_balance,
        cash_balance=row.cash_balance,
        realized_pnl=row.realized_pnl,
        unrealized_pnl=row.unrealized_pnl,
        fees=row.fees,
        equity=row.equity,
        equity_peak=row.equity_peak,
        drawdown=row.drawdown,
        drawdown_pct=row.drawdown_pct,
    )


def _position(row: PaperPositionModel) -> PositionSnapshot:
    return PositionSnapshot(
        instrument_id=row.instrument_id,
        broker_id=row.broker_id,
        specification_hash=row.specification_hash,
        symbol=row.symbol,
        quote_currency=row.quote_currency,
        net_quantity=row.net_quantity,
        average_price=row.average_price,
        mark_price=row.mark_price,
        contract_multiplier=row.contract_multiplier,
        realized_pnl=row.realized_pnl,
        unrealized_pnl=row.unrealized_pnl,
        fees=row.fees,
    )


def _set_position(
    row: PaperPositionModel,
    value: PositionSnapshot,
    now: datetime,
    event_at: datetime,
) -> None:
    for name in (
        "quote_currency",
        "net_quantity",
        "average_price",
        "mark_price",
        "contract_multiplier",
        "realized_pnl",
        "unrealized_pnl",
        "fees",
    ):
        setattr(row, name, getattr(value, name))
    row.updated_at = now
    row.last_event_at = event_at
    row.version = (row.version or 0) + 1


def _set_account(row: PaperAccountModel, value: AccountSnapshot, now: datetime) -> None:
    for name in (
        "cash_balance",
        "realized_pnl",
        "unrealized_pnl",
        "fees",
        "equity",
        "equity_peak",
        "drawdown",
        "drawdown_pct",
    ):
        setattr(row, name, getattr(value, name))
    row.updated_at = now
    row.version += 1


def _require_same_fill(row: PaperAccountingEventModel, fill: AccountingFill) -> None:
    persisted = (
        row.symbol,
        row.instrument_id,
        row.broker_id,
        row.specification_hash,
        row.side,
        row.quantity,
        row.price,
        row.commission,
        _utc(row.filled_at),
        row.quote_currency,
        row.contract_multiplier,
    )
    candidate = (
        fill.symbol,
        fill.instrument_id,
        fill.broker_id,
        fill.specification_hash,
        fill.side,
        fill.quantity,
        fill.price,
        fill.commission,
        _utc(fill.filled_at),
        fill.quote_currency,
        fill.contract_multiplier,
    )
    if persisted != candidate:
        raise ValueError("accounting evidence key was reused with different fill data")


__all__ = ["PostgresPaperAccountingRepository"]
