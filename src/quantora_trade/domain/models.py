"""Immutable domain value objects and entities."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from quantora_trade.domain.enums import Action, AssetClass, TradingMode


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


def _require_positive(value: Decimal, field_name: str) -> None:
    if value <= Decimal("0"):
        raise ValueError(f"{field_name} must be greater than zero")


@dataclass(frozen=True, slots=True)
class Instrument:
    """Broker-normalized symbol specification."""

    symbol: str
    asset_class: AssetClass
    quote_currency: str
    digits: int
    point: Decimal
    pip_size: Decimal
    tick_size: Decimal
    tick_value: Decimal
    contract_size: Decimal
    spread_points: int
    session_timezone: str
    session_profile: str
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if self.digits < 0:
            raise ValueError("digits must be non-negative")
        for field_name in (
            "point",
            "pip_size",
            "tick_size",
            "tick_value",
            "contract_size",
            "volume_min",
            "volume_max",
            "volume_step",
        ):
            _require_positive(getattr(self, field_name), field_name)
        if self.spread_points < 0:
            raise ValueError("spread_points must be non-negative")
        if not self.session_timezone.strip():
            raise ValueError("session_timezone must not be empty")
        if not self.session_profile.strip():
            raise ValueError("session_profile must not be empty")
        if self.volume_min > self.volume_max:
            raise ValueError("volume_min must not exceed volume_max")


@dataclass(frozen=True, slots=True)
class Candle:
    """A closed or forming OHLCV bar."""

    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tick_volume: int | None
    is_closed: bool

    def __post_init__(self) -> None:
        _require_utc(self.open_time, "open_time")
        _require_utc(self.close_time, "close_time")
        if self.close_time <= self.open_time:
            raise ValueError("close_time must be after open_time")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high violates OHLC invariant")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low violates OHLC invariant")
        if self.tick_volume is not None and self.tick_volume < 0:
            raise ValueError("tick_volume must be non-negative")


@dataclass(frozen=True, slots=True)
class Signal:
    """Immutable candidate signal produced by a strategy."""

    id: UUID
    symbol: str
    timeframe: str
    action: Action
    confidence: Decimal
    strategy_version: str
    reason_codes: tuple[str, ...]
    observed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.observed_at, "observed_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.observed_at:
            raise ValueError("expires_at must be after observed_at")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class Decision:
    """Final deterministic trading decision before risk assessment."""

    id: UUID
    signal_id: UUID
    symbol: str
    timeframe: str
    action: Action
    confidence: Decimal
    policy_version: str
    reason_codes: tuple[str, ...]
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.expires_at, "expires_at")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Immutable result returned by the deterministic risk engine."""

    id: UUID
    decision_id: UUID
    policy_version: str
    approved: bool
    rejection_codes: tuple[str, ...]
    risk_amount: Decimal
    volume: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    created_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        if self.risk_amount < Decimal("0") or self.volume < Decimal("0"):
            raise ValueError("risk_amount and volume must be non-negative")
        if self.approved and (self.volume <= Decimal("0") or self.stop_loss is None):
            raise ValueError("approved assessment requires volume and stop_loss")
        if self.approved and self.rejection_codes:
            raise ValueError("approved assessment cannot contain rejection codes")
        if not self.approved and not self.rejection_codes:
            raise ValueError("rejected assessment requires rejection codes")


@dataclass(frozen=True, slots=True)
class ApprovedOrderIntent:
    """The only domain object accepted by a broker submission port."""

    id: UUID
    risk_assessment_id: UUID
    idempotency_key: str
    mode: TradingMode
    symbol: str
    side: Action
    volume: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None
    created_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        if self.side is Action.HOLD:
            raise ValueError("order side cannot be HOLD")
        _require_positive(self.volume, "volume")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be empty")
