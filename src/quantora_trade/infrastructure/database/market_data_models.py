"""SQLAlchemy models for raw and normalized market data."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from quantora_trade.infrastructure.database.models import Base


class BrokerModel(Base):
    """Market-data or execution broker reference."""

    __tablename__ = "brokers"
    __table_args__ = {"schema": "quantora"}

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(50), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstrumentModel(Base):
    """Broker-specific symbol specification."""

    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("broker_id", "symbol", name="uq_instruments_broker_symbol"),
        CheckConstraint("digits >= 0", name="digits_non_negative"),
        CheckConstraint("point > 0", name="point_positive"),
        CheckConstraint("tick_size > 0", name="tick_size_positive"),
        CheckConstraint("tick_value > 0", name="tick_value_positive"),
        CheckConstraint("contract_size > 0", name="contract_size_positive"),
        CheckConstraint("volume_min > 0", name="volume_min_positive"),
        CheckConstraint("volume_max >= volume_min", name="volume_range_valid"),
        CheckConstraint("volume_step > 0", name="volume_step_positive"),
        {"schema": "quantora"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    broker_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quantora.brokers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    canonical_symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(30), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    digits: Mapped[int] = mapped_column(Integer, nullable=False)
    point: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    tick_value: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    contract_size: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    volume_min: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    volume_max: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    volume_step: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    specification_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RawMarketRateModel(Base):
    """Append-only provider payload and extracted values."""

    __tablename__ = "raw_market_rates"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "timeframe",
            "open_time",
            "source",
            "payload_hash",
            name="uq_raw_rate_lineage",
        ),
        CheckConstraint("high >= low", name="raw_high_not_below_low"),
        CheckConstraint("tick_volume >= 0", name="raw_tick_volume_non_negative"),
        Index("ix_raw_rates_lookup", "instrument_id", "timeframe", "open_time"),
        {"schema": "quantora"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quantora.instruments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    tick_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spread_points: Mapped[int] = mapped_column(Integer, nullable=False)
    real_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CandleModel(Base):
    """Normalized candle used by strategy and backtest layers."""

    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "timeframe",
            "open_time",
            "source",
            name="uq_candle_source_time",
        ),
        CheckConstraint("close_time > open_time", name="candle_time_range_valid"),
        CheckConstraint("high >= low", name="candle_high_not_below_low"),
        CheckConstraint("tick_volume IS NULL OR tick_volume >= 0", name="tick_volume_non_negative"),
        Index("ix_candles_lookup", "instrument_id", "timeframe", "open_time"),
        {"schema": "quantora"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quantora.instruments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(24, 12), nullable=False)
    tick_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spread_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketDataIssueModel(Base):
    """Persisted data-quality finding."""

    __tablename__ = "market_data_issues"
    __table_args__ = (
        Index("ix_market_data_issues_lookup", "instrument_id", "timeframe", "detected_at"),
        {"schema": "quantora"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quantora.instruments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    issue_code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    candle_open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
