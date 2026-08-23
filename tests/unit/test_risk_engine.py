"""Phase 5 deterministic risk-engine tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from quantora_trade.domain.enums import Action, AssetClass, RiskRejectionCode
from quantora_trade.domain.models import Decision, Instrument
from quantora_trade.risk import (
    AccountRiskSnapshot,
    ExitPolicy,
    RiskEngine,
    RiskPolicy,
    TradeRiskRequest,
)
from quantora_trade.risk.exposure import ExposureLimits, OpenExposure

NOW = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)


def policy(**overrides: object) -> RiskPolicy:
    values: dict[str, object] = {
        "version": "risk-research-v1",
        "risk_per_trade": Decimal("0.01"),
        "max_daily_loss_fraction": Decimal("0.03"),
        "max_drawdown_fraction": Decimal("0.10"),
        "max_portfolio_open_risk_fraction": Decimal("0.04"),
        "max_spread_points": 50,
        "max_slippage_points": 10,
        "min_stop_ticks": Decimal("10"),
        "max_stop_ticks": Decimal("1000"),
        "min_reward_risk": Decimal("1.5"),
        "max_open_positions": 5,
        "max_consecutive_losses": 3,
        "cooldown": timedelta(hours=2),
        "minimum_margin_buffer_fraction": Decimal("0.20"),
        "snapshot_max_age": timedelta(seconds=30),
    }
    values.update(overrides)
    return RiskPolicy(**values)  # type: ignore[arg-type]


def instrument(**overrides: object) -> Instrument:
    values: dict[str, object] = {
        "symbol": "XAUUSD",
        "asset_class": AssetClass.METAL,
        "quote_currency": "USD",
        "digits": 2,
        "point": Decimal("0.01"),
        "pip_size": Decimal("0.01"),
        "tick_size": Decimal("0.01"),
        "tick_value": Decimal("1"),
        "contract_size": Decimal("100"),
        "spread_points": 25,
        "session_timezone": "UTC",
        "session_profile": "metals_24x5",
        "volume_min": Decimal("0.01"),
        "volume_max": Decimal("100"),
        "volume_step": Decimal("0.01"),
    }
    values.update(overrides)
    return Instrument(**values)  # type: ignore[arg-type]


def decision(**overrides: object) -> Decision:
    values: dict[str, object] = {
        "id": uuid4(),
        "signal_id": uuid4(),
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "action": Action.BUY,
        "confidence": Decimal("0.8"),
        "policy_version": "decision-v1",
        "reason_codes": ("H1_BULLISH_CONTEXT",),
        "expires_at": NOW + timedelta(minutes=15),
    }
    values.update(overrides)
    return Decision(**values)  # type: ignore[arg-type]


def account(**overrides: object) -> AccountRiskSnapshot:
    values: dict[str, object] = {
        "equity": Decimal("10000"),
        "free_margin": Decimal("9000"),
        "daily_peak_equity": Decimal("10000"),
        "account_peak_equity": Decimal("10000"),
        "open_risk": Decimal("0"),
        "open_positions": 0,
        "consecutive_losses": 0,
        "reconciled_at": NOW,
    }
    values.update(overrides)
    return AccountRiskSnapshot(**values)  # type: ignore[arg-type]


def request(**overrides: object) -> TradeRiskRequest:
    values: dict[str, object] = {
        "decision": decision(),
        "instrument": instrument(),
        "account": account(),
        "entry": Decimal("2400"),
        "stop_loss": Decimal("2399"),
        "take_profit": Decimal("2403"),
        "observed_spread_points": 25,
        "margin_per_lot": Decimal("1000"),
        "assessed_at": NOW,
        "strategy_key": "trend-v1",
        "existing_exposures": (),
        "exposure_limits": ExposureLimits(gross_notional=Decimal("1000000")),
        "system_ready": True,
        "database_available": True,
        "broker_connected": True,
        "position_reconciled": True,
        "market_open": True,
        "session_allowed": True,
        "news_blocked": False,
        "expected_slippage_points": 5,
    }
    values.update(overrides)
    return TradeRiskRequest(**values)  # type: ignore[arg-type]


def rejection_values(result_codes: tuple[str, ...]) -> set[str]:
    return set(result_codes)


def test_approved_trade_sizes_from_equity_and_rounds_volume_down() -> None:
    result = RiskEngine(policy()).assess(request())

    assert result.approved is True
    assert result.volume == Decimal("0.76")
    assert result.risk_amount == Decimal("98.80")
    assert result.stop_loss == Decimal("2399")


def test_assessment_is_deterministic_for_identical_inputs() -> None:
    engine = RiskEngine(policy())
    proposed = request()

    assert engine.assess(proposed) == engine.assess(proposed)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"kill_switch_active": True}, RiskRejectionCode.KILL_SWITCH_ACTIVE),
        (
            {"assessed_at": NOW + timedelta(minutes=16)},
            RiskRejectionCode.DECISION_EXPIRED,
        ),
        (
            {"account": account(reconciled_at=NOW - timedelta(minutes=1))},
            RiskRejectionCode.STALE_DATA,
        ),
        ({"observed_spread_points": 51}, RiskRejectionCode.SPREAD_TOO_WIDE),
        ({"system_ready": False}, RiskRejectionCode.SYSTEM_NOT_READY),
        ({"database_available": False}, RiskRejectionCode.DATABASE_UNAVAILABLE),
        ({"broker_connected": False}, RiskRejectionCode.BROKER_DISCONNECTED),
        ({"position_reconciled": False}, RiskRejectionCode.POSITION_NOT_RECONCILED),
        ({"market_open": False}, RiskRejectionCode.MARKET_CLOSED),
        ({"session_allowed": False}, RiskRejectionCode.SESSION_BLOCKED),
        ({"news_blocked": True}, RiskRejectionCode.NEWS_BLOCK),
        ({"expected_slippage_points": 11}, RiskRejectionCode.SLIPPAGE_RISK_HIGH),
        ({"stop_loss": Decimal("2401")}, RiskRejectionCode.INVALID_STOP_LOSS),
        ({"stop_loss": Decimal("2400")}, RiskRejectionCode.INVALID_STOP_LOSS),
        ({"stop_loss": Decimal("2399.95")}, RiskRejectionCode.STOP_DISTANCE_TOO_SMALL),
        ({"stop_loss": Decimal("2389")}, RiskRejectionCode.STOP_DISTANCE_TOO_LARGE),
        ({"take_profit": Decimal("2402")}, RiskRejectionCode.LOW_REWARD_RISK),
    ],
)
def test_hard_gate_rejects_and_zeroes_risk(
    overrides: dict[str, object], code: RiskRejectionCode
) -> None:
    result = RiskEngine(policy()).assess(request(**overrides))

    assert result.approved is False
    assert code.value in result.rejection_codes
    assert result.volume == Decimal("0")
    assert result.risk_amount == Decimal("0")
    assert result.stop_loss is None


def test_sell_requires_stop_above_entry_and_target_below() -> None:
    proposed = request(
        decision=decision(action=Action.SELL),
        stop_loss=Decimal("2401"),
        take_profit=Decimal("2397"),
    )

    result = RiskEngine(policy()).assess(proposed)

    assert result.approved is True
    assert result.volume == Decimal("0.76")


def test_portfolio_budget_uses_remaining_open_risk() -> None:
    existing = OpenExposure(
        symbol="XAUUSD",
        strategy="trend-v1",
        asset_class=AssetClass.METAL,
        action=Action.BUY,
        volume=Decimal("1"),
        contract_size=Decimal("100"),
        price=Decimal("2400"),
        monetary_risk=Decimal("350"),
        stop_loss=Decimal("2399"),
    )
    proposed = request(account=account(open_risk=Decimal("350")), existing_exposures=(existing,))

    result = RiskEngine(policy()).assess(proposed)

    assert result.approved is True
    assert result.volume == Decimal("0.38")
    assert result.risk_amount == Decimal("49.40")


@pytest.mark.parametrize(
    ("account_override", "code"),
    [
        ({"equity": Decimal("9699")}, RiskRejectionCode.DAILY_LOSS_LIMIT),
        (
            {"equity": Decimal("8999"), "daily_peak_equity": Decimal("9000")},
            RiskRejectionCode.DRAWDOWN_LIMIT,
        ),
        ({"open_positions": 5}, RiskRejectionCode.POSITION_LIMIT),
        (
            {"consecutive_losses": 3, "last_loss_at": NOW - timedelta(hours=1)},
            RiskRejectionCode.CONSECUTIVE_LOSS_COOLDOWN,
        ),
    ],
)
def test_account_level_limits_reject(
    account_override: dict[str, object], code: RiskRejectionCode
) -> None:
    result = RiskEngine(policy()).assess(request(account=account(**account_override)))

    assert code.value in rejection_values(result.rejection_codes)
    assert result.approved is False


def test_cooldown_releases_after_configured_duration() -> None:
    snapshot = account(consecutive_losses=3, last_loss_at=NOW - timedelta(hours=2))

    result = RiskEngine(policy()).assess(request(account=snapshot))

    assert RiskRejectionCode.CONSECUTIVE_LOSS_COOLDOWN.value not in result.rejection_codes
    assert result.approved is True


def test_missing_last_loss_time_fails_closed_at_cooldown_threshold() -> None:
    result = RiskEngine(policy()).assess(
        request(account=account(consecutive_losses=3, last_loss_at=None))
    )

    assert RiskRejectionCode.RISK_INPUT_INCOMPLETE.value in result.rejection_codes
    assert result.approved is False


def test_future_reconciliation_time_fails_closed() -> None:
    result = RiskEngine(policy()).assess(
        request(account=account(reconciled_at=NOW + timedelta(microseconds=1)))
    )

    assert RiskRejectionCode.RISK_INPUT_INCOMPLETE.value in result.rejection_codes


def test_expiry_boundary_is_exclusive() -> None:
    expiring = decision(expires_at=NOW)

    result = RiskEngine(policy()).assess(request(decision=expiring))

    assert RiskRejectionCode.DECISION_EXPIRED.value in result.rejection_codes


def test_materially_different_protective_target_has_distinct_assessment_id() -> None:
    proposed = request()
    changed = request(
        decision=proposed.decision,
        instrument=proposed.instrument,
        account=proposed.account,
        take_profit=Decimal("2404"),
    )

    assert RiskEngine(policy()).assess(proposed).id != RiskEngine(policy()).assess(changed).id


def test_volume_below_broker_minimum_rejects_without_rounding_up() -> None:
    result = RiskEngine(policy(risk_per_trade=Decimal("0.00001"))).assess(request())

    assert RiskRejectionCode.VOLUME_BELOW_MINIMUM.value in result.rejection_codes
    assert result.volume == Decimal("0")


def test_margin_buffer_rejects_trade_that_would_consume_reserved_margin() -> None:
    result = RiskEngine(policy()).assess(
        request(account=account(free_margin=Decimal("1000")), margin_per_lot=Decimal("1100"))
    )

    assert RiskRejectionCode.INSUFFICIENT_MARGIN.value in result.rejection_codes
    assert result.approved is False


def test_pending_and_open_exposure_are_included_in_what_if_limit() -> None:
    existing = OpenExposure(
        symbol="XAUUSD",
        strategy="trend-v1",
        asset_class=AssetClass.METAL,
        action=Action.BUY,
        volume=Decimal("1"),
        contract_size=Decimal("100"),
        price=Decimal("2400"),
        monetary_risk=Decimal("100"),
        stop_loss=Decimal("2399"),
    )
    limits = ExposureLimits(
        gross_notional=Decimal("1000000"),
        per_currency=(("USD", Decimal("300000")),),
    )

    result = RiskEngine(policy()).assess(
        request(
            account=account(open_risk=Decimal("100")),
            existing_exposures=(existing,),
            exposure_limits=limits,
        )
    )

    assert RiskRejectionCode.CURRENCY_EXPOSURE_LIMIT.value in result.rejection_codes
    assert result.approved is False


def test_reconciled_risk_scalar_must_equal_open_and_pending_records() -> None:
    result = RiskEngine(policy()).assess(request(account=account(open_risk=Decimal("1"))))
    assert RiskRejectionCode.RISK_INPUT_INCOMPLETE.value in result.rejection_codes


def test_targetless_trade_requires_explicit_versioned_exit_policy() -> None:
    rejected = RiskEngine(policy()).assess(request(take_profit=None))
    approved = RiskEngine(policy()).assess(
        request(
            take_profit=None,
            exit_policy=ExitPolicy("trailing-exit-v1", timedelta(hours=8)),
        )
    )
    assert RiskRejectionCode.RISK_INPUT_INCOMPLETE.value in rejected.rejection_codes
    assert approved.approved is True


def test_reward_risk_is_net_of_all_explicit_price_costs() -> None:
    result = RiskEngine(policy()).assess(
        request(
            take_profit=Decimal("2402"),
            commission_price_cost=Decimal("0.10"),
            swap_price_cost=Decimal("0.10"),
        )
    )
    assert RiskRejectionCode.LOW_REWARD_RISK.value in result.rejection_codes


def test_costs_increase_loss_per_lot_and_reduce_volume() -> None:
    with_costs = RiskEngine(policy()).assess(request())
    without_costs = RiskEngine(policy()).assess(
        request(observed_spread_points=0, expected_slippage_points=0)
    )
    assert with_costs.volume < without_costs.volume
    assert with_costs.risk_amount <= Decimal("100")


def test_missing_fx_conversion_rejects_instead_of_raising() -> None:
    existing = OpenExposure(
        symbol="EURJPY",
        strategy="fx-v1",
        asset_class=AssetClass.FOREX,
        action=Action.BUY,
        volume=Decimal("1"),
        contract_size=Decimal("100000"),
        price=Decimal("160"),
        monetary_risk=Decimal("100"),
        stop_loss=Decimal("159"),
    )
    result = RiskEngine(policy()).assess(
        request(account=account(open_risk=Decimal("100")), existing_exposures=(existing,))
    )
    assert RiskRejectionCode.RISK_INPUT_INCOMPLETE.value in result.rejection_codes
