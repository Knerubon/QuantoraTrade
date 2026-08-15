"""Pydantic configuration schemas with safe trading defaults."""

from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from quantora_trade.domain.enums import AssetClass, TradingMode


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
    """Risk limits required before paper or live trading may start."""

    version: str
    risk_per_trade: Decimal | None = Field(default=None, gt=0, lt=1)
    daily_loss_limit: Decimal | None = Field(default=None, gt=0, lt=1)
    max_drawdown: Decimal | None = Field(default=None, gt=0, lt=1)
    max_open_positions: int | None = Field(default=None, gt=0)

    def missing_limits(self) -> tuple[str, ...]:
        required = (
            "risk_per_trade",
            "daily_loss_limit",
            "max_drawdown",
            "max_open_positions",
        )
        return tuple(name for name in required if getattr(self, name) is None)


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
    def live_mode_requires_explicit_enablement(self) -> "AppSettings":
        if self.trading_mode is TradingMode.LIVE and not self.live_trading_enabled:
            raise ValueError("live mode requires QUANTORA_LIVE_TRADING_ENABLED=true")
        return self
