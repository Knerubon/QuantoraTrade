"""Pydantic configuration schemas with safe trading defaults."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from quantora_trade.domain.enums import AssetClass, TradingMode

if TYPE_CHECKING:
    from quantora_trade.risk.models import RiskPolicy


class SymbolSettings(BaseModel):
    """Per-symbol configuration; broker specifications are loaded at runtime."""

    asset_class: AssetClass
    enabled: bool = False
    timeframes: tuple[str, ...] = ("M15", "H1")
    risk_profile: str
    session_timezone: str = "UTC"
    session_profile: str = "broker_defined"
    max_spread_points: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_timeframes(self) -> "SymbolSettings":
        if not self.timeframes:
            raise ValueError("at least one timeframe is required")
        if len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError("timeframes must be unique")
        if not self.session_timezone.strip() or not self.session_profile.strip():
            raise ValueError("session identity must not be empty")
        return self


class RiskPolicySettings(BaseModel):
    """Validated control-plane values; incomplete policies remain unusable."""

    version: str
    risk_per_trade: Decimal | None = Field(default=None, gt=0, lt=1)
    daily_loss_limit: Decimal | None = Field(default=None, gt=0, lt=1)
    max_drawdown: Decimal | None = Field(default=None, gt=0, lt=1)
    max_portfolio_open_risk: Decimal | None = Field(default=None, gt=0, lt=1)
    max_open_positions: int | None = Field(default=None, gt=0)
    max_spread_points: int | None = Field(default=None, ge=0)
    max_slippage_points: int | None = Field(default=None, ge=0)
    min_stop_ticks: Decimal | None = Field(default=None, gt=0)
    max_stop_ticks: Decimal | None = Field(default=None, gt=0)
    min_reward_risk: Decimal | None = Field(default=None, gt=0)
    max_consecutive_losses: int | None = Field(default=None, gt=0)
    cooldown_seconds: int | None = Field(default=None, gt=0)
    minimum_margin_buffer: Decimal | None = Field(default=None, ge=0, lt=1)
    snapshot_max_age_seconds: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_stop_range(self) -> "RiskPolicySettings":
        if (
            self.min_stop_ticks is not None
            and self.max_stop_ticks is not None
            and self.min_stop_ticks > self.max_stop_ticks
        ):
            raise ValueError("min_stop_ticks must not exceed max_stop_ticks")
        return self

    def missing_limits(self) -> tuple[str, ...]:
        required = (
            "risk_per_trade",
            "daily_loss_limit",
            "max_drawdown",
            "max_portfolio_open_risk",
            "max_open_positions",
            "max_spread_points",
            "max_slippage_points",
            "min_stop_ticks",
            "max_stop_ticks",
            "min_reward_risk",
            "max_consecutive_losses",
            "cooldown_seconds",
            "minimum_margin_buffer",
            "snapshot_max_age_seconds",
        )
        return tuple(name for name in required if getattr(self, name) is None)

    def to_domain(self) -> "RiskPolicy":
        """Compile FE/API input into an immutable policy, rejecting drafts."""

        from quantora_trade.risk.models import RiskPolicy

        missing = self.missing_limits()
        if missing:
            raise ValueError(f"risk policy is incomplete: {', '.join(missing)}")
        return RiskPolicy(
            version=self.version,
            risk_per_trade=self._decimal("risk_per_trade"),
            max_daily_loss_fraction=self._decimal("daily_loss_limit"),
            max_drawdown_fraction=self._decimal("max_drawdown"),
            max_portfolio_open_risk_fraction=self._decimal("max_portfolio_open_risk"),
            max_spread_points=self._integer("max_spread_points"),
            max_slippage_points=self._integer("max_slippage_points"),
            min_stop_ticks=self._decimal("min_stop_ticks"),
            max_stop_ticks=self._decimal("max_stop_ticks"),
            min_reward_risk=self._decimal("min_reward_risk"),
            max_open_positions=self._integer("max_open_positions"),
            max_consecutive_losses=self._integer("max_consecutive_losses"),
            cooldown=timedelta(seconds=self._integer("cooldown_seconds")),
            minimum_margin_buffer_fraction=self._decimal("minimum_margin_buffer"),
            snapshot_max_age=timedelta(seconds=self._integer("snapshot_max_age_seconds")),
        )

    def _decimal(self, name: str) -> Decimal:
        value = getattr(self, name)
        if not isinstance(value, Decimal):
            raise ValueError(f"{name} is required")
        return value

    def _integer(self, name: str) -> int:
        value = getattr(self, name)
        if not isinstance(value, int):
            raise ValueError(f"{name} is required")
        return value


class AppSettings(BaseSettings):
    """Environment settings loaded once at application startup."""

    model_config = SettingsConfigDict(
        env_prefix="QUANTORA_",
        env_file=".env",
        extra="forbid",
    )

    environment: str = "development"
    trading_mode: TradingMode = TradingMode.BACKTEST
    config_dir: Path = Path("config")
    database_url: str = "postgresql+psycopg://quantora:change-me@localhost:5432/quantora"
    live_trading_enabled: bool = False

    @model_validator(mode="after")
    def live_mode_is_closed_for_current_phase(self) -> "AppSettings":
        if self.trading_mode is TradingMode.LIVE:
            raise ValueError("live mode is unavailable in the current phase")
        return self
