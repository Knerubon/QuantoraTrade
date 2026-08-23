"""Immutable inputs for deterministic pre-trade risk assessment."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantora_trade.domain.models import Decision, Instrument
from quantora_trade.risk.exposure import ExposureLimits, OpenExposure, PendingExposure


def _finite_non_negative(value: Decimal, name: str) -> None:
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


def _fraction(value: Decimal, name: str, *, allow_zero: bool = False) -> None:
    lower_ok = value >= 0 if allow_zero else value > 0
    if not value.is_finite() or not lower_ok or value > 1:
        raise ValueError(f"{name} must be a valid fraction")


def _utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Versioned hard limits; values are research/backtest inputs, not Live approval."""

    version: str
    risk_per_trade: Decimal
    max_daily_loss_fraction: Decimal
    max_drawdown_fraction: Decimal
    max_portfolio_open_risk_fraction: Decimal
    max_spread_points: int
    max_slippage_points: int
    min_stop_ticks: Decimal
    max_stop_ticks: Decimal
    min_reward_risk: Decimal
    max_open_positions: int
    max_consecutive_losses: int
    cooldown: timedelta
    minimum_margin_buffer_fraction: Decimal
    snapshot_max_age: timedelta

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("policy version must not be empty")
        for name in (
            "risk_per_trade",
            "max_daily_loss_fraction",
            "max_drawdown_fraction",
            "max_portfolio_open_risk_fraction",
        ):
            _fraction(getattr(self, name), name)
        _fraction(
            self.minimum_margin_buffer_fraction,
            "minimum_margin_buffer_fraction",
            allow_zero=True,
        )
        if self.max_spread_points < 0 or self.max_slippage_points < 0:
            raise ValueError("spread and slippage points must be non-negative")
        for name in ("min_stop_ticks", "max_stop_ticks", "min_reward_risk"):
            value = getattr(self, name)
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if self.min_stop_ticks > self.max_stop_ticks:
            raise ValueError("min_stop_ticks must not exceed max_stop_ticks")
        if self.max_open_positions <= 0 or self.max_consecutive_losses <= 0:
            raise ValueError("position and loss limits must be greater than zero")
        if self.cooldown <= timedelta(0) or self.snapshot_max_age <= timedelta(0):
            raise ValueError("cooldown and snapshot_max_age must be greater than zero")


@dataclass(frozen=True, slots=True)
class AccountRiskSnapshot:
    """Point-in-time reconciled account state used by every hard gate."""

    equity: Decimal
    free_margin: Decimal
    daily_peak_equity: Decimal
    account_peak_equity: Decimal
    open_risk: Decimal
    open_positions: int
    consecutive_losses: int
    reconciled_at: datetime
    last_loss_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "equity",
            "free_margin",
            "daily_peak_equity",
            "account_peak_equity",
            "open_risk",
        ):
            _finite_non_negative(getattr(self, name), name)
        if self.equity <= 0 or self.daily_peak_equity <= 0 or self.account_peak_equity <= 0:
            raise ValueError("equity and peak equity values must be greater than zero")
        if self.daily_peak_equity < self.equity or self.account_peak_equity < self.equity:
            raise ValueError("peak equity values must not be below current equity")
        if self.open_positions < 0 or self.consecutive_losses < 0:
            raise ValueError("position and loss counts must be non-negative")
        _utc(self.reconciled_at, "reconciled_at")
        if self.last_loss_at is not None:
            _utc(self.last_loss_at, "last_loss_at")


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    """Versioned bounded-exit contract for trades without a fixed target."""

    version: str
    max_holding_period: timedelta

    def __post_init__(self) -> None:
        if not self.version.strip() or self.version != self.version.strip():
            raise ValueError("exit policy version must be a non-empty trimmed value")
        if self.max_holding_period <= timedelta(0):
            raise ValueError("max_holding_period must be greater than zero")


@dataclass(frozen=True, slots=True)
class TradeRiskRequest:
    """Proposed protective levels plus broker inputs for one decision."""

    decision: Decision
    instrument: Instrument
    account: AccountRiskSnapshot
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None
    observed_spread_points: int
    margin_per_lot: Decimal
    assessed_at: datetime
    strategy_key: str
    existing_exposures: tuple[OpenExposure | PendingExposure, ...]
    exposure_limits: ExposureLimits
    system_ready: bool
    database_available: bool
    broker_connected: bool
    position_reconciled: bool
    market_open: bool
    session_allowed: bool
    news_blocked: bool
    expected_slippage_points: int
    kill_switch_active: bool = False
    commission_price_cost: Decimal = Decimal("0")
    swap_price_cost: Decimal = Decimal("0")
    exit_policy: ExitPolicy | None = None

    def __post_init__(self) -> None:
        for name in ("entry", "stop_loss", "margin_per_lot"):
            value = getattr(self, name)
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if self.take_profit is not None and (
            not self.take_profit.is_finite() or self.take_profit <= 0
        ):
            raise ValueError("take_profit must be finite and greater than zero")
        if self.observed_spread_points < 0:
            raise ValueError("observed_spread_points must be non-negative")
        if self.expected_slippage_points < 0:
            raise ValueError("expected_slippage_points must be non-negative")
        for name in ("commission_price_cost", "swap_price_cost"):
            _finite_non_negative(getattr(self, name), name)
        if not self.strategy_key.strip() or self.strategy_key != self.strategy_key.strip():
            raise ValueError("strategy_key must be a non-empty trimmed value")
        _utc(self.assessed_at, "assessed_at")
