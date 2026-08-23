"""Property tests for Phase 5 risk invariants.

These tests deliberately exercise only the public risk API.  They protect the
monotonic sizing and fail-closed guarantees across a wider input space than the
example-based risk-engine tests.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from quantora_trade.domain.enums import Action, AssetClass
from quantora_trade.domain.models import Decision, Instrument
from quantora_trade.risk import AccountRiskSnapshot, RiskEngine, RiskPolicy, TradeRiskRequest
from quantora_trade.risk.exposure import ExposureLimits, OpenExposure

NOW = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)
DECISION_ID = UUID("12345678-1234-5678-1234-567812345678")
SIGNAL_ID = UUID("87654321-4321-8765-4321-876543218765")


def _policy() -> RiskPolicy:
    return RiskPolicy(
        version="risk-property-v1",
        risk_per_trade=Decimal("0.01"),
        max_daily_loss_fraction=Decimal("0.03"),
        max_drawdown_fraction=Decimal("0.10"),
        max_portfolio_open_risk_fraction=Decimal("0.04"),
        max_spread_points=50,
        max_slippage_points=10,
        min_stop_ticks=Decimal("10"),
        max_stop_ticks=Decimal("1000"),
        min_reward_risk=Decimal("1.5"),
        max_open_positions=5,
        max_consecutive_losses=3,
        cooldown=timedelta(hours=2),
        minimum_margin_buffer_fraction=Decimal("0.20"),
        snapshot_max_age=timedelta(seconds=30),
    )


def _instrument() -> Instrument:
    return Instrument(
        symbol="XAUUSD",
        asset_class=AssetClass.METAL,
        quote_currency="USD",
        digits=2,
        point=Decimal("0.01"),
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        tick_value=Decimal("1"),
        contract_size=Decimal("100"),
        spread_points=25,
        session_timezone="UTC",
        session_profile="metals_24x5",
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
    )


def _account(
    *, equity: Decimal = Decimal("10000"), open_risk: Decimal = Decimal("0")
) -> AccountRiskSnapshot:
    return AccountRiskSnapshot(
        equity=equity,
        free_margin=equity,
        daily_peak_equity=equity,
        account_peak_equity=equity,
        open_risk=open_risk,
        open_positions=0,
        consecutive_losses=0,
        reconciled_at=NOW,
    )


def _request(
    *,
    equity: Decimal = Decimal("10000"),
    open_risk: Decimal = Decimal("0"),
    stop_ticks: int = 100,
) -> TradeRiskRequest:
    entry = Decimal("2400")
    stop_distance = Decimal(stop_ticks) * _instrument().tick_size
    decision = Decision(
        id=DECISION_ID,
        signal_id=SIGNAL_ID,
        symbol="XAUUSD",
        timeframe="M15",
        action=Action.BUY,
        confidence=Decimal("0.8"),
        policy_version="decision-v1",
        reason_codes=("H1_BULLISH_CONTEXT",),
        expires_at=NOW + timedelta(minutes=15),
    )
    existing = ()
    if open_risk > 0:
        existing = (
            OpenExposure(
                symbol="XAUUSD",
                strategy="existing-v1",
                asset_class=AssetClass.METAL,
                action=Action.BUY,
                volume=Decimal("0.01"),
                contract_size=Decimal("100"),
                price=entry,
                monetary_risk=open_risk,
                stop_loss=entry - Decimal("1"),
            ),
        )
    return TradeRiskRequest(
        decision=decision,
        instrument=_instrument(),
        account=_account(equity=equity, open_risk=open_risk),
        entry=entry,
        stop_loss=entry - stop_distance,
        take_profit=entry + (stop_distance * Decimal("2")),
        observed_spread_points=25,
        margin_per_lot=Decimal("10"),
        assessed_at=NOW,
        strategy_key="trend-v1",
        existing_exposures=existing,
        exposure_limits=ExposureLimits(gross_notional=Decimal("1000000000")),
        system_ready=True,
        database_available=True,
        broker_connected=True,
        position_reconciled=True,
        market_open=True,
        session_allowed=True,
        news_blocked=False,
        expected_slippage_points=5,
    )


@settings(max_examples=100, deadline=None)
@given(
    narrower=st.integers(min_value=10, max_value=999),
    extra_width=st.integers(min_value=1, max_value=1000),
)
def test_wider_stop_never_increases_approved_volume(narrower: int, extra_width: int) -> None:
    wider = min(1000, narrower + extra_width)
    engine = RiskEngine(_policy())

    narrow_result = engine.assess(_request(stop_ticks=narrower))
    wide_result = engine.assess(_request(stop_ticks=wider))

    if narrow_result.approved and wide_result.approved:
        assert wide_result.volume <= narrow_result.volume


@settings(max_examples=100, deadline=None)
@given(
    lower_equity=st.integers(min_value=1000, max_value=9999),
    equity_increase=st.integers(min_value=1, max_value=90000),
    stop_ticks=st.integers(min_value=10, max_value=1000),
)
def test_lower_equity_never_increases_risk_or_volume(
    lower_equity: int, equity_increase: int, stop_ticks: int
) -> None:
    lower = Decimal(lower_equity)
    higher = lower + Decimal(equity_increase)
    engine = RiskEngine(_policy())

    lower_result = engine.assess(_request(equity=lower, stop_ticks=stop_ticks))
    higher_result = engine.assess(_request(equity=higher, stop_ticks=stop_ticks))

    assert lower_result.risk_amount <= higher_result.risk_amount
    assert lower_result.volume <= higher_result.volume


@settings(max_examples=100, deadline=None)
@given(
    equity=st.integers(min_value=1000, max_value=100000),
    used_basis_points=st.integers(min_value=0, max_value=399),
    stop_ticks=st.integers(min_value=10, max_value=1000),
)
def test_approved_risk_never_exceeds_trade_or_remaining_portfolio_budget(
    equity: int, used_basis_points: int, stop_ticks: int
) -> None:
    equity_value = Decimal(equity)
    open_risk = equity_value * Decimal(used_basis_points) / Decimal("10000")
    policy = _policy()
    result = RiskEngine(policy).assess(
        _request(equity=equity_value, open_risk=open_risk, stop_ticks=stop_ticks)
    )
    per_trade = equity_value * policy.risk_per_trade
    portfolio_remaining = equity_value * policy.max_portfolio_open_risk_fraction - open_risk

    if result.approved:
        assert result.risk_amount <= min(per_trade, portfolio_remaining)


@settings(max_examples=50, deadline=None)
@given(
    equity=st.integers(min_value=1000, max_value=100000),
    stop_ticks=st.integers(min_value=10, max_value=1000),
)
def test_exact_request_replay_is_deterministic(equity: int, stop_ticks: int) -> None:
    proposed = _request(equity=Decimal(equity), stop_ticks=stop_ticks)
    engine = RiskEngine(_policy())

    assert engine.assess(proposed) == engine.assess(proposed)


@settings(max_examples=50, deadline=None)
@given(
    rejected_field=st.sampled_from(
        (
            "kill_switch_active",
            "system_ready",
            "database_available",
            "broker_connected",
            "position_reconciled",
        )
    )
)
def test_system_data_or_kill_rejection_removes_all_risk_and_protection(
    rejected_field: str,
) -> None:
    proposed = _request()
    if rejected_field == "kill_switch_active":
        rejected = replace(proposed, kill_switch_active=True)
    elif rejected_field == "system_ready":
        rejected = replace(proposed, system_ready=False)
    elif rejected_field == "database_available":
        rejected = replace(proposed, database_available=False)
    elif rejected_field == "broker_connected":
        rejected = replace(proposed, broker_connected=False)
    else:
        rejected = replace(proposed, position_reconciled=False)

    result = RiskEngine(_policy()).assess(rejected)

    assert result.approved is False
    assert result.volume == Decimal("0")
    assert result.risk_amount == Decimal("0")
    assert result.stop_loss is None
    assert result.take_profit is None
