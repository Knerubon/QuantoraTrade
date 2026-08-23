"""Tests for the deterministic Signal-to-Decision boundary."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from quantora_trade.domain.enums import Action, SignalReasonCode
from quantora_trade.domain.models import Signal
from quantora_trade.risk.decision import (
    BELOW_DECISION_CONFIDENCE,
    SIGNAL_EXPIRED,
    SIGNAL_HOLD,
    DecisionEngine,
    DecisionPolicy,
)

OBSERVED_AT = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)


def signal(
    *,
    action: Action = Action.BUY,
    confidence: Decimal = Decimal("0.80"),
    reason_codes: tuple[str, ...] = (SignalReasonCode.H1_BULLISH_CONTEXT.value,),
) -> Signal:
    return Signal(
        id=UUID("db50dabe-1bbf-51c7-b3d3-53718777ea91"),
        symbol="XAUUSD",
        timeframe="M15",
        action=action,
        confidence=confidence,
        strategy_version="trend-v1",
        reason_codes=reason_codes,
        observed_at=OBSERVED_AT,
        expires_at=OBSERVED_AT + timedelta(minutes=15),
    )


def engine(minimum_confidence: str = "0.70") -> DecisionEngine:
    return DecisionEngine(
        DecisionPolicy(version="decision-v1", minimum_confidence=Decimal(minimum_confidence))
    )


def test_eligible_direction_and_signal_lineage_are_preserved() -> None:
    candidate = signal()

    result = engine().decide(signal=candidate, evaluated_at=OBSERVED_AT)

    assert result.action is Action.BUY
    assert result.confidence == candidate.confidence
    assert result.reason_codes == candidate.reason_codes
    assert result.signal_id == candidate.id
    assert result.expires_at == candidate.expires_at


def test_decision_identity_is_deterministic() -> None:
    candidate = signal()
    policy_engine = engine()

    first = policy_engine.decide(signal=candidate, evaluated_at=OBSERVED_AT)
    second = policy_engine.decide(
        signal=candidate, evaluated_at=OBSERVED_AT + timedelta(seconds=30)
    )

    assert first == second


def test_below_configured_confidence_downgrades_without_side_flip() -> None:
    candidate = signal(
        action=Action.SELL,
        confidence=Decimal("0.69"),
        reason_codes=(SignalReasonCode.H1_BEARISH_CONTEXT.value,),
    )

    result = engine().decide(signal=candidate, evaluated_at=OBSERVED_AT)

    assert result.action is Action.HOLD
    assert result.reason_codes == (*candidate.reason_codes, BELOW_DECISION_CONFIDENCE)


def test_hold_signal_stays_hold_with_lineage() -> None:
    candidate = signal(
        action=Action.HOLD,
        reason_codes=(SignalReasonCode.INSUFFICIENT_EVIDENCE.value,),
    )

    result = engine().decide(signal=candidate, evaluated_at=OBSERVED_AT)

    assert result.action is Action.HOLD
    assert result.reason_codes == (*candidate.reason_codes, SIGNAL_HOLD)


def test_expired_signal_is_fail_closed_at_exact_expiry() -> None:
    candidate = signal()

    result = engine().decide(signal=candidate, evaluated_at=candidate.expires_at)

    assert result.action is Action.HOLD
    assert result.reason_codes == (*candidate.reason_codes, SIGNAL_EXPIRED)


@pytest.mark.parametrize(
    "minimum_confidence, error",
    [
        (Decimal("-0.01"), ValueError),
        (Decimal("1.01"), ValueError),
        (Decimal("NaN"), ValueError),
        (0.7, TypeError),
    ],
)
def test_policy_rejects_invalid_or_non_decimal_thresholds(
    minimum_confidence: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        DecisionPolicy(  # type: ignore[arg-type]
            version="decision-v1", minimum_confidence=minimum_confidence
        )


def test_policy_is_versioned_immutable_and_nonempty() -> None:
    policy = DecisionPolicy(version="decision-v1", minimum_confidence=Decimal("0.70"))
    assert DecisionEngine(policy).policy is policy

    with pytest.raises(FrozenInstanceError):
        policy.version = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="version"):
        DecisionPolicy(version=" ", minimum_confidence=Decimal("0.70"))


@pytest.mark.parametrize(
    "evaluated_at",
    [
        datetime(2026, 8, 23, 2, 0),
        datetime(2026, 8, 23, 9, 0, tzinfo=timezone(timedelta(hours=7))),
    ],
)
def test_evaluation_clock_must_be_utc(evaluated_at: datetime) -> None:
    with pytest.raises(ValueError, match="evaluated_at must be timezone-aware UTC"):
        engine().decide(signal=signal(), evaluated_at=evaluated_at)
