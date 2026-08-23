"""Explicit dashboard DTOs; deliberately exclude credentials and broker payloads."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quantora_trade.domain.enums import TradingMode


class DashboardModel(BaseModel):
    """Immutable public read model with strict input validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OperationalState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class WorkerView(DashboardModel):
    worker_id: str = Field(min_length=1)
    state: OperationalState
    last_heartbeat_at: datetime


class KillSwitchView(DashboardModel):
    active: bool
    scope: str = Field(min_length=1)
    reason_code: str | None = None
    changed_at: datetime | None = None


class DependencyView(DashboardModel):
    component: str = Field(min_length=1)
    state: OperationalState
    last_success_at: datetime | None = None
    age_seconds: int | None = Field(default=None, ge=0)


class OrderView(DashboardModel):
    order_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: str = Field(pattern="^(BUY|SELL)$")
    status: str = Field(min_length=1)
    quantity: Decimal = Field(ge=0)
    filled_quantity: Decimal = Field(ge=0)
    created_at: datetime


class FillView(DashboardModel):
    fill_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    filled_at: datetime


class PositionView(DashboardModel):
    symbol: str = Field(min_length=1)
    net_quantity: Decimal
    average_price: Decimal = Field(ge=0)
    unrealized_pnl: Decimal


class PnlView(DashboardModel):
    currency: str = Field(min_length=3, max_length=3)
    realized: Decimal
    unrealized: Decimal
    fees: Decimal = Field(ge=0)
    cash_balance: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    equity_peak: Decimal = Decimal("0")
    drawdown: Decimal = Field(default=Decimal("0"), ge=0)
    drawdown_pct: Decimal = Field(default=Decimal("0"), ge=0)


class ExposureView(DashboardModel):
    currency: str = Field(min_length=3, max_length=3)
    gross: Decimal = Field(ge=0)
    net: Decimal


class DashboardSnapshot(DashboardModel):
    generated_at: datetime
    mode: TradingMode
    worker: WorkerView
    kill_switch: KillSwitchView
    dependencies: tuple[DependencyView, ...] = ()
    orders: tuple[OrderView, ...] = ()
    fills: tuple[FillView, ...] = ()
    positions: tuple[PositionView, ...] = ()
    pnl: tuple[PnlView, ...] = ()
    exposure: tuple[ExposureView, ...] = ()
    degraded_reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_canonical_order(self) -> "DashboardSnapshot":
        expected = tuple(sorted(set(self.degraded_reason_codes)))
        if self.degraded_reason_codes != expected:
            raise ValueError("degraded_reason_codes must be unique and sorted")
        return self


class DashboardEventKind(StrEnum):
    WORKER = "worker"
    KILL_SWITCH = "kill_switch"
    DEPENDENCY = "dependency"
    ORDER = "order"
    FILL = "fill"
    POSITION = "position"
    PNL = "pnl"
    EXPOSURE = "exposure"


class DashboardEvent(DashboardModel):
    event_id: int = Field(gt=0)
    occurred_at: datetime
    kind: DashboardEventKind
    reason_code: str = Field(min_length=1)
    entity_id: str | None = None


class DashboardEventPage(DashboardModel):
    events: tuple[DashboardEvent, ...]
    next_cursor: int


class TradeView(DashboardModel):
    event_id: int = Field(gt=0)
    order_id: str = Field(min_length=1)
    fill_sequence: int = Field(gt=0)
    symbol: str = Field(min_length=1)
    side: str = Field(pattern="^(BUY|SELL)$")
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    commission: Decimal = Field(ge=0)
    realized_pnl: Decimal
    filled_at: datetime


class TradePage(DashboardModel):
    trades: tuple[TradeView, ...]
    next_cursor: int


class PaperReport(DashboardModel):
    generated_at: datetime
    mode: TradingMode
    currency: str = Field(min_length=3, max_length=3)
    initial_balance: Decimal
    cash_balance: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    fees: Decimal = Field(ge=0)
    equity: Decimal
    equity_peak: Decimal
    drawdown: Decimal = Field(ge=0)
    drawdown_pct: Decimal = Field(ge=0)
    fill_count: int = Field(ge=0)
    open_position_count: int = Field(ge=0)
