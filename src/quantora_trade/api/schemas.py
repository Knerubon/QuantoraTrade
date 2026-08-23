"""Public, versioned API schemas.

Monetary and ratio decimals cross the HTTP boundary as strings so JavaScript
clients never silently round risk limits.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from quantora_trade.config.settings import RiskPolicySettings
from quantora_trade.domain.enums import TradingMode

DecimalString = Annotated[
    Decimal,
    PlainSerializer(lambda value: str(value), return_type=str, when_used="json"),
]


class StrictApiModel(BaseModel):
    """Base for public requests: unknown keys must never mutate configuration."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictApiModel):
    status: str


class ServiceStatus(StrictApiModel):
    """Deterministic, secret-free operational snapshot."""

    service: str = "quantora-trade"
    version: str
    environment: str
    mode: TradingMode
    ready: bool
    database_ready: bool
    broker_connected: bool
    kill_switch_active: bool
    worker_state: str | None = None
    data_connected: bool | None = None
    enabled_symbols: tuple[str, ...] = ()
    enabled_timeframes: tuple[str, ...] = ()
    active_strategy_version: str | None = None
    active_config_version: str | None = None
    code_version: str | None = None
    open_positions: int | None = Field(default=None, ge=0)
    open_orders: int | None = Field(default=None, ge=0)
    degraded_reason_codes: tuple[str, ...] = ()


class RiskPolicyDraft(StrictApiModel):
    version: str = Field(min_length=1)
    risk_per_trade: DecimalString | None = Field(default=None, gt=0, lt=1)
    daily_loss_limit: DecimalString | None = Field(default=None, gt=0, lt=1)
    max_drawdown: DecimalString | None = Field(default=None, gt=0, lt=1)
    max_portfolio_open_risk: DecimalString | None = Field(default=None, gt=0, lt=1)
    max_open_positions: int | None = Field(default=None, gt=0)
    max_spread_points: int | None = Field(default=None, ge=0)
    max_slippage_points: int | None = Field(default=None, ge=0)
    min_stop_ticks: DecimalString | None = Field(default=None, gt=0)
    max_stop_ticks: DecimalString | None = Field(default=None, gt=0)
    min_reward_risk: DecimalString | None = Field(default=None, gt=0)
    max_consecutive_losses: int | None = Field(default=None, gt=0)
    cooldown_seconds: int | None = Field(default=None, gt=0)
    minimum_margin_buffer: DecimalString | None = Field(default=None, ge=0, lt=1)
    snapshot_max_age_seconds: int | None = Field(default=None, gt=0)

    @field_validator(
        "risk_per_trade",
        "daily_loss_limit",
        "max_drawdown",
        "max_portfolio_open_risk",
        "min_stop_ticks",
        "max_stop_ticks",
        "min_reward_risk",
        "minimum_margin_buffer",
        mode="before",
    )
    @classmethod
    def decimal_must_arrive_as_string(cls, value: Any) -> Any:
        if value is None:
            return value
        if not isinstance(value, str):
            raise ValueError("decimal values must be JSON strings")
        try:
            return Decimal(value)
        except InvalidOperation as error:
            raise ValueError("invalid decimal string") from error

    def to_settings(self) -> RiskPolicySettings:
        return RiskPolicySettings.model_validate(self.model_dump())


class RiskPolicyValidationRequest(StrictApiModel):
    policy: RiskPolicyDraft
    requested_mode: TradingMode = TradingMode.PAPER
    activate: bool = False


class RiskPolicyValidationResponse(StrictApiModel):
    structurally_valid: bool = True
    activation_ready: bool
    missing_limits: tuple[str, ...]
    requested_mode: TradingMode
    policy: RiskPolicyDraft


class SystemCommandRequest(StrictApiModel):
    """Typed immutable payload accepted by the PAPER command queue."""

    mode: TradingMode = TradingMode.PAPER
    symbols: tuple[str, ...] = Field(min_length=1)
    strategy_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("symbols")
    @classmethod
    def canonical_symbols(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or value != value.strip().upper() for value in values):
            raise ValueError("symbols must be canonical trimmed uppercase values")
        if len(set(values)) != len(values):
            raise ValueError("symbols must be unique")
        return values

    @field_validator("strategy_id", "reason")
    @classmethod
    def trimmed_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("value must be trimmed")
        return value


class SystemCommandResponse(StrictApiModel):
    id: UUID
    request_id: str
    action: str
    mode: TradingMode
    symbols: tuple[str, ...]
    strategy_id: str
    reason: str
    symbol_specifications: tuple["ResolvedSymbolSpecification", ...] = ()
    actor: str
    status: str
    created_at: datetime
    updated_at: datetime
    replayed: bool = False


class ResolvedSymbolSpecification(StrictApiModel):
    """Immutable identity selected by the authoritative start preflight."""

    symbol: str = Field(min_length=1, max_length=40)
    specification_id: UUID
    specification_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    quote_currency: str = Field(pattern=r"^[A-Z]{3}$")
    active: bool = True
    stale: bool = False
