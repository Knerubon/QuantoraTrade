"""Tests for the mandatory RiskAssessment → ApprovedOrderIntent boundary."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.domain.models import Decision, RiskAssessment
from quantora_trade.risk.approval import build_approved_order_intent

NOW = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)


def decision(*, action: Action = Action.BUY) -> Decision:
    return Decision(
        id=uuid4(),
        signal_id=uuid4(),
        symbol="XAUUSD",
        timeframe="M15",
        action=action,
        confidence=Decimal("0.8"),
        policy_version="decision-v1",
        reason_codes=("H1_BULLISH_CONTEXT",),
        expires_at=NOW + timedelta(minutes=15),
    )


def assessment(candidate: Decision, *, approved: bool = True) -> RiskAssessment:
    return RiskAssessment(
        id=uuid4(),
        decision_id=candidate.id,
        policy_version="risk-v1",
        approved=approved,
        rejection_codes=() if approved else ("SPREAD_TOO_WIDE",),
        risk_amount=Decimal("100") if approved else Decimal("0"),
        volume=Decimal("1") if approved else Decimal("0"),
        stop_loss=Decimal("2399") if approved else None,
        take_profit=Decimal("2402") if approved else None,
        created_at=NOW,
    )


def test_approved_matching_assessment_builds_deterministic_intent() -> None:
    candidate = decision()
    checked = assessment(candidate)

    first = build_approved_order_intent(
        decision=candidate,
        assessment=checked,
        mode=TradingMode.BACKTEST,
        created_at=NOW,
    )
    replay = build_approved_order_intent(
        decision=candidate,
        assessment=checked,
        mode=TradingMode.BACKTEST,
        created_at=NOW + timedelta(seconds=1),
    )

    assert first.id == replay.id
    assert first.idempotency_key == replay.idempotency_key
    assert first.risk_assessment_id == checked.id
    assert first.volume == checked.volume
    assert first.stop_loss == checked.stop_loss


def test_rejected_assessment_has_no_order_route() -> None:
    candidate = decision()

    with pytest.raises(ValueError, match="rejected risk assessment"):
        build_approved_order_intent(
            decision=candidate,
            assessment=assessment(candidate, approved=False),
            mode=TradingMode.BACKTEST,
            created_at=NOW,
        )


def test_assessment_for_other_decision_is_rejected() -> None:
    candidate = decision()

    with pytest.raises(ValueError, match="does not belong"):
        build_approved_order_intent(
            decision=candidate,
            assessment=assessment(decision()),
            mode=TradingMode.BACKTEST,
            created_at=NOW,
        )


def test_hold_and_expired_decisions_cannot_create_intent() -> None:
    hold = decision(action=Action.HOLD)
    with pytest.raises(ValueError, match="HOLD"):
        build_approved_order_intent(
            decision=hold,
            assessment=assessment(hold),
            mode=TradingMode.BACKTEST,
            created_at=NOW,
        )

    candidate = decision()
    with pytest.raises(ValueError, match="expired"):
        build_approved_order_intent(
            decision=candidate,
            assessment=assessment(candidate),
            mode=TradingMode.BACKTEST,
            created_at=candidate.expires_at,
        )


def test_future_assessment_is_rejected() -> None:
    candidate = decision()
    checked = RiskAssessment(
        id=uuid4(),
        decision_id=candidate.id,
        policy_version="risk-v1",
        approved=True,
        rejection_codes=(),
        risk_amount=Decimal("100"),
        volume=Decimal("1"),
        stop_loss=Decimal("2399"),
        take_profit=Decimal("2402"),
        created_at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="future"):
        build_approved_order_intent(
            decision=candidate,
            assessment=checked,
            mode=TradingMode.BACKTEST,
            created_at=NOW,
        )
