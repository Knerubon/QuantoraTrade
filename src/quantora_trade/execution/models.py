"""Immutable value objects for deterministic PAPER execution."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from quantora_trade.domain.enums import Action, TradingMode


def _utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")


def _positive(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")


class OrderStatus(StrEnum):
    CREATED = "created"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InstrumentExecutionSnapshot:
    """Authoritative broker specification frozen at order submission."""

    instrument_id: UUID
    broker_id: UUID
    specification_hash: str
    quote_currency: str
    contract_multiplier: Decimal
    point: Decimal

    def __post_init__(self) -> None:
        if len(self.specification_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.specification_hash
        ):
            raise ValueError("specification_hash must be a SHA-256 hex digest")
        if (
            self.quote_currency != self.quote_currency.strip().upper()
            or len(self.quote_currency) != 3
        ):
            raise ValueError("quote_currency must be canonical ISO-style uppercase")
        _positive(self.contract_multiplier, "contract_multiplier")
        _positive(self.point, "point")


@dataclass(frozen=True, slots=True)
class PaperOrderRequest:
    """A PAPER-only request copied from an approved-order boundary."""

    approved_intent_id: UUID
    idempotency_key: str
    mode: TradingMode
    symbol: str
    side: Action
    volume: Decimal
    instrument: InstrumentExecutionSnapshot
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.mode is not TradingMode.PAPER:
            raise ValueError("paper adapter accepts PAPER mode only")
        if self.side not in {Action.BUY, Action.SELL}:
            raise ValueError("paper order side must be BUY or SELL")
        if self.symbol != self.symbol.strip().upper() or not self.symbol:
            raise ValueError("symbol must be canonical uppercase")
        if not self.idempotency_key.strip() or self.idempotency_key != self.idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty trimmed value")
        _positive(self.volume, "volume")
        _utc(self.expires_at, "expires_at")

    @property
    def point(self) -> Decimal:
        return self.instrument.point


@dataclass(frozen=True, slots=True)
class PaperQuote:
    symbol: str
    bid: Decimal
    ask: Decimal
    available_volume: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.symbol != self.symbol.strip().upper() or not self.symbol:
            raise ValueError("quote symbol must be canonical uppercase")
        _positive(self.bid, "bid")
        _positive(self.ask, "ask")
        if self.ask < self.bid:
            raise ValueError("ask must not be below bid")
        if not self.available_volume.is_finite() or self.available_volume < 0:
            raise ValueError("available_volume must be finite and non-negative")
        _utc(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class Fill:
    volume: Decimal
    price: Decimal
    commission: Decimal
    filled_at: datetime

    def __post_init__(self) -> None:
        _positive(self.volume, "fill volume")
        _positive(self.price, "fill price")
        if not self.commission.is_finite() or self.commission < 0:
            raise ValueError("commission must be finite and non-negative")
        _utc(self.filled_at, "filled_at")


@dataclass(frozen=True, slots=True)
class OrderEvent:
    sequence: int
    status: OrderStatus
    occurred_at: datetime
    code: str

    def __post_init__(self) -> None:
        if self.sequence <= 0 or not self.code.strip():
            raise ValueError("event sequence and code must be valid")
        _utc(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True)
class PaperOrder:
    id: UUID
    request_hash: str
    request: PaperOrderRequest
    status: OrderStatus
    filled_volume: Decimal
    fills: tuple[Fill, ...]
    events: tuple[OrderEvent, ...]

    @property
    def remaining_volume(self) -> Decimal:
        return self.request.volume - self.filled_volume
