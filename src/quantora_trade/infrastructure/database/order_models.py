"""SQLAlchemy models for durable PAPER orders, events, and fills."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from quantora_trade.infrastructure.database.models import Base


class PaperOrderModel(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (
        CheckConstraint("mode = 'paper'", name="paper_mode_only"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash_valid"),
        CheckConstraint("specification_hash ~ '^[0-9a-f]{64}$'", name="specification_hash_valid"),
        CheckConstraint("quote_currency ~ '^[A-Z]{3}$'", name="quote_currency_valid"),
        CheckConstraint("contract_multiplier > 0", name="contract_multiplier_positive"),
        CheckConstraint("requested_volume > 0 AND point > 0", name="positive_request_values"),
        CheckConstraint(
            "filled_volume >= 0 AND filled_volume <= requested_volume", name="filled_volume_valid"
        ),
        CheckConstraint("side IN ('buy', 'sell')", name="side_valid"),
        CheckConstraint(
            "status IN ('created','accepted','partial','filled','cancel_pending',"
            "'cancelled','rejected','expired','unknown')",
            name="status_valid",
        ),
        UniqueConstraint("request_hash", name="uq_paper_orders_request_hash"),
        {"schema": "quantora"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_intent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    instrument_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    broker_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    specification_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    contract_multiplier: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    requested_volume: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    point: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    filled_volume: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class PaperOrderEventModel(Base):
    __tablename__ = "paper_order_events"
    __table_args__ = (
        CheckConstraint("sequence > 0", name="positive_sequence"),
        CheckConstraint(
            "status IN ('created','accepted','partial','filled','cancel_pending',"
            "'cancelled','rejected','expired','unknown')",
            name="status_valid",
        ),
        UniqueConstraint("order_id", "sequence", name="uq_paper_order_events_sequence"),
        {"schema": "quantora"},
    )

    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quantora.paper_orders.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)


class PaperFillModel(Base):
    __tablename__ = "paper_fills"
    __table_args__ = (
        CheckConstraint("sequence > 0", name="positive_sequence"),
        CheckConstraint("volume > 0 AND price > 0 AND commission >= 0", name="valid_values"),
        UniqueConstraint("order_id", "sequence", name="uq_paper_fills_sequence"),
        {"schema": "quantora"},
    )

    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quantora.paper_orders.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    volume: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["PaperFillModel", "PaperOrderEventModel", "PaperOrderModel"]
