"""SQLAlchemy models for durable PAPER portfolio accounting."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from quantora_trade.infrastructure.database.models import Base


class PaperAccountModel(Base):
    __tablename__ = "paper_accounts"
    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_valid"),
        CheckConstraint("fees >= 0 AND drawdown >= 0 AND drawdown_pct >= 0", name="totals_valid"),
        CheckConstraint(
            "equity_peak >= equity AND drawdown = equity_peak - equity",
            name="drawdown_valid",
        ),
        {"schema": "quantora"},
    )

    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    initial_balance: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    equity_peak: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    drawdown: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(30, 18), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class PaperPositionModel(Base):
    __tablename__ = "paper_positions"
    __table_args__ = (
        CheckConstraint("quote_currency ~ '^[A-Z]{3}$'", name="currency_valid"),
        CheckConstraint("average_price >= 0 AND mark_price >= 0", name="prices_valid"),
        CheckConstraint("contract_multiplier > 0 AND fees >= 0", name="values_valid"),
        CheckConstraint(
            "net_quantity <> 0 OR (average_price = 0 AND unrealized_pnl = 0)",
            name="flat_position_valid",
        ),
        {"schema": "quantora"},
    )

    instrument_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    broker_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    specification_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    net_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    average_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    mark_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    contract_multiplier: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class PaperAccountingEventModel(Base):
    __tablename__ = "paper_accounting_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["order_id", "fill_sequence"],
            ["quantora.paper_fills.order_id", "quantora.paper_fills.sequence"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("side IN ('buy', 'sell')", name="side_valid"),
        CheckConstraint("quantity > 0 AND price > 0 AND commission >= 0", name="fill_valid"),
        CheckConstraint("contract_multiplier > 0", name="multiplier_valid"),
        CheckConstraint("post_drawdown >= 0", name="drawdown_valid"),
        UniqueConstraint("order_id", "fill_sequence", name="uq_accounting_fill_evidence"),
        {"schema": "quantora"},
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    fill_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    instrument_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    broker_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    specification_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    contract_multiplier: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    realized_delta: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    post_net_quantity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    post_average_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    post_cash_balance: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    post_equity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    post_equity_peak: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    post_drawdown: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperMarkEventModel(Base):
    """Append-only price observation and resulting accounting projection."""

    __tablename__ = "paper_mark_events"
    __table_args__ = (
        CheckConstraint("price > 0", name="price_valid"),
        CheckConstraint("post_drawdown >= 0", name="drawdown_valid"),
        UniqueConstraint("instrument_id", "observed_at", name="uq_paper_mark_observation"),
        {"schema": "quantora"},
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quantora.paper_positions.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    broker_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    specification_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prior_mark_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    post_unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    post_equity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    post_equity_peak: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    post_drawdown: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "PaperAccountModel",
    "PaperAccountingEventModel",
    "PaperMarkEventModel",
    "PaperPositionModel",
]
