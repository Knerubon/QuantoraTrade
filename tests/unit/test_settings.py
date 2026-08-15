"""Unit tests for safe configuration defaults."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from quantora_trade.config.settings import AppSettings, RiskPolicySettings, SymbolSettings
from quantora_trade.domain.enums import AssetClass, TradingMode


def test_app_settings_by_default_disables_live_trading() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.trading_mode is TradingMode.BACKTEST
    assert settings.live_trading_enabled is False


def test_app_settings_when_live_is_not_enabled_rejects_live_mode() -> None:
    with pytest.raises(ValidationError, match="live mode requires"):
        AppSettings(
            _env_file=None,
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=False,
        )


def test_risk_policy_reports_missing_required_limits() -> None:
    policy = RiskPolicySettings(version="risk-draft-v1")

    assert policy.missing_limits() == (
        "risk_per_trade",
        "daily_loss_limit",
        "max_drawdown",
        "max_open_positions",
    )


def test_risk_policy_accepts_complete_backtest_limits() -> None:
    policy = RiskPolicySettings(
        version="risk-backtest-v1",
        risk_per_trade=Decimal("0.005"),
        daily_loss_limit=Decimal("0.02"),
        max_drawdown=Decimal("0.10"),
        max_open_positions=3,
    )

    assert policy.missing_limits() == ()


def test_symbol_settings_accepts_session_and_spread_policy() -> None:
    settings = SymbolSettings(
        asset_class=AssetClass.METAL,
        enabled=True,
        timeframes=("M15", "H1"),
        risk_profile="gold_default",
        session_timezone="UTC",
        session_profile="metals_24x5",
        max_spread_points=80,
    )

    assert settings.session_profile == "metals_24x5"
    assert settings.max_spread_points == 80


def test_symbol_settings_rejects_non_positive_spread_limit() -> None:
    with pytest.raises(ValidationError, match="max_spread_points"):
        SymbolSettings(
            asset_class=AssetClass.FOREX,
            risk_profile="forex_major",
            max_spread_points=0,
        )
