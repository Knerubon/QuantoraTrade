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


@pytest.mark.parametrize("enabled", [False, True])
def test_app_settings_hard_rejects_live_mode_in_current_phase(enabled: bool) -> None:
    with pytest.raises(ValidationError, match="live mode is unavailable"):
        AppSettings(
            _env_file=None,
            trading_mode=TradingMode.LIVE,
            live_trading_enabled=enabled,
        )


def test_risk_policy_reports_missing_required_limits() -> None:
    policy = RiskPolicySettings(version="risk-draft-v1")

    assert policy.missing_limits() == (
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


def test_risk_policy_accepts_complete_backtest_limits() -> None:
    policy = RiskPolicySettings(
        version="risk-backtest-v1",
        risk_per_trade=Decimal("0.005"),
        daily_loss_limit=Decimal("0.02"),
        max_drawdown=Decimal("0.10"),
        max_portfolio_open_risk=Decimal("0.04"),
        max_open_positions=3,
        max_spread_points=50,
        max_slippage_points=10,
        min_stop_ticks=Decimal("10"),
        max_stop_ticks=Decimal("1000"),
        min_reward_risk=Decimal("1.5"),
        max_consecutive_losses=3,
        cooldown_seconds=7200,
        minimum_margin_buffer=Decimal("0.2"),
        snapshot_max_age_seconds=30,
    )

    assert policy.missing_limits() == ()
    domain = policy.to_domain()
    assert domain.version == "risk-backtest-v1"
    assert domain.cooldown.total_seconds() == 7200


def test_incomplete_risk_policy_cannot_compile_for_runtime() -> None:
    policy = RiskPolicySettings(version="risk-ui-draft-v1")

    with pytest.raises(ValueError, match="risk policy is incomplete"):
        policy.to_domain()


def test_risk_policy_rejects_inverted_stop_range() -> None:
    with pytest.raises(ValidationError, match="min_stop_ticks"):
        RiskPolicySettings(
            version="risk-invalid-v1",
            min_stop_ticks=Decimal("100"),
            max_stop_ticks=Decimal("10"),
        )


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
