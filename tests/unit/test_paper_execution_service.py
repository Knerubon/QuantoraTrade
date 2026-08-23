"""End-to-end safety proofs for the approved-intent to PAPER bridge."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent, Decision, RiskAssessment
from quantora_trade.execution import (
    DeterministicPaperAdapter,
    InstrumentExecutionSnapshot,
    OrderStatus,
    PaperBrokerPort,
    PaperExecutionInput,
    PaperFillPolicy,
    PaperQuote,
)
from quantora_trade.risk.approval import build_approved_order_intent
from quantora_trade.risk.kill_switch import (
    InMemoryKillSwitchRepository,
    KillSwitchScope,
    KillSwitchScopeKind,
    KillSwitchService,
)
from quantora_trade.risk.submission import (
    OrderSubmissionService,
    SubmissionClaim,
    SubmissionClaimState,
    SubmissionContext,
)

NOW = datetime(2026, 8, 23, 5, tzinfo=UTC)


@dataclass
class Clock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class Inputs:
    def __init__(self) -> None:
        self.calls = 0

    def execution_input(self, intent: ApprovedOrderIntent) -> PaperExecutionInput:
        self.calls += 1
        return PaperExecutionInput(
            instrument=InstrumentExecutionSnapshot(
                uuid4(), uuid4(), "a" * 64, "USD", Decimal("100"), Decimal("0.01")
            ),
            quote=PaperQuote(
                symbol=intent.symbol,
                bid=Decimal("2500.00"),
                ask=Decimal("2500.20"),
                available_volume=intent.volume,
                observed_at=NOW,
            ),
            expires_at=NOW + timedelta(minutes=10),
        )


class Evidence:
    def __init__(self, decision: Decision, assessment: RiskAssessment) -> None:
        self._decision = decision
        self._assessment = assessment

    def decision(self, decision_id: UUID) -> Decision | None:
        return self._decision if decision_id == self._decision.id else None

    def assessment(self, assessment_id: UUID) -> RiskAssessment | None:
        return self._assessment if assessment_id == self._assessment.id else None

    def submission_context(self, assessment_id: UUID) -> SubmissionContext | None:
        if assessment_id != self._assessment.id:
            return None
        return SubmissionContext(account="paper-1", asset="METAL", strategy="trend-v1")


class Journal:
    def __init__(self) -> None:
        self.results: dict[str, object | None] = {}

    def claim(self, key: str, request_hash: str) -> SubmissionClaim:
        assert len(request_hash) == 64
        if key not in self.results:
            self.results[key] = None
            return SubmissionClaim(SubmissionClaimState.ACQUIRED)
        result = self.results[key]
        if result is None:
            return SubmissionClaim(SubmissionClaimState.IN_FLIGHT)
        return SubmissionClaim(SubmissionClaimState.COMPLETED, result)  # type: ignore[arg-type]

    def complete(self, key: str, result: object) -> None:
        self.results[key] = result

    def abandon(self, key: str) -> None:
        self.results.pop(key, None)


def evidence() -> tuple[Decision, RiskAssessment]:
    decision = Decision(
        id=uuid4(),
        signal_id=uuid4(),
        symbol="XAUUSD",
        timeframe="M15",
        action=Action.BUY,
        confidence=Decimal("0.8"),
        policy_version="decision-v1",
        reason_codes=("H1_BULLISH_CONTEXT",),
        expires_at=NOW + timedelta(minutes=10),
    )
    assessment = RiskAssessment(
        id=uuid4(),
        decision_id=decision.id,
        policy_version="risk-v1",
        approved=True,
        rejection_codes=(),
        risk_amount=Decimal("100"),
        volume=Decimal("1"),
        stop_loss=Decimal("2490"),
        take_profit=Decimal("2520"),
        created_at=NOW,
    )
    return decision, assessment


def system() -> tuple[OrderSubmissionService, PaperBrokerPort, Inputs, KillSwitchService, Evidence]:
    decision, assessment = evidence()
    trusted_clock = Clock()
    inputs = Inputs()
    broker = PaperBrokerPort(
        inputs=inputs,
        adapter=DeterministicPaperAdapter(
            clock=trusted_clock,
            policy=PaperFillPolicy(slippage_points=2),
        ),
        clock=trusted_clock,
    )
    kill_switch = KillSwitchService(InMemoryKillSwitchRepository())
    stored = Evidence(decision, assessment)
    service = OrderSubmissionService(
        evidence=stored,
        journal=Journal(),
        kill_switch=kill_switch,
        broker=broker,
        clock=trusted_clock,
        decision_policy_version="decision-v1",
        risk_policy_version="risk-v1",
    )
    return service, broker, inputs, kill_switch, stored


def intent(stored: Evidence, mode: TradingMode = TradingMode.PAPER) -> ApprovedOrderIntent:
    return build_approved_order_intent(
        decision=stored._decision,
        assessment=stored._assessment,
        mode=mode,
        created_at=NOW,
    )


def test_approved_evidence_fills_through_submission_service_and_is_queryable() -> None:
    service, broker, inputs, _, stored = system()
    approved = intent(stored)

    result = service.submit(approved)
    order = broker.get_order(approved.idempotency_key)

    assert result.external_order_id == str(order.id)
    assert order.status is OrderStatus.FILLED
    assert order.request.approved_intent_id == approved.id
    assert order.fills[0].price == Decimal("2500.22")
    assert inputs.calls == 1
    assert service.submit(approved).external_order_id == result.external_order_id
    assert inputs.calls == 1
    recovered = broker.lookup(approved.idempotency_key, "a" * 64)
    assert recovered is not None and recovered.external_order_id == result.external_order_id
    assert broker.lookup("absent", "a" * 64) is None


def test_kill_switch_and_live_never_reach_paper_inputs_or_adapter() -> None:
    service, broker, inputs, kill_switch, stored = system()
    approved = intent(stored)
    kill_switch.activate(
        KillSwitchScope(KillSwitchScopeKind.SYMBOL, "XAUUSD"),
        occurred_at=NOW,
        actor="risk",
        reason="incident",
        incident_reference="INC-6",
    )
    with pytest.raises(PermissionError, match="kill switch"):
        service.submit(approved)
    with pytest.raises(PermissionError, match="LIVE"):
        service.submit(intent(stored, TradingMode.LIVE))
    assert inputs.calls == 0
    with pytest.raises(KeyError):
        broker.get_order(approved.idempotency_key)


def test_rejected_risk_cannot_create_intent_or_reach_paper() -> None:
    _, _, inputs, _, stored = system()
    rejected = replace(
        stored._assessment,
        approved=False,
        rejection_codes=("KILL_SWITCH_ACTIVE",),
        volume=Decimal("0"),
        stop_loss=None,
    )
    with pytest.raises(ValueError, match="rejected risk"):
        build_approved_order_intent(
            decision=stored._decision,
            assessment=rejected,
            mode=TradingMode.PAPER,
            created_at=NOW,
        )
    assert inputs.calls == 0


def test_paper_broker_fails_closed_before_adapter_for_untrusted_inputs() -> None:
    _, broker, inputs, _, stored = system()
    with pytest.raises(TypeError, match="ApprovedOrderIntent"):
        broker.submit(object())  # type: ignore[arg-type]
    with pytest.raises(PermissionError, match="PAPER"):
        broker.submit(intent(stored, TradingMode.LIVE))
    assert inputs.calls == 0


def test_paper_execution_input_validates_point_and_expiry() -> None:
    quote = PaperQuote(
        symbol="XAUUSD",
        bid=Decimal("1"),
        ask=Decimal("1"),
        available_volume=Decimal("1"),
        observed_at=NOW,
    )
    with pytest.raises(ValueError, match="point"):
        InstrumentExecutionSnapshot(
            uuid4(), uuid4(), "a" * 64, "USD", Decimal("100"), Decimal("NaN")
        )
    with pytest.raises(ValueError, match="UTC"):
        PaperExecutionInput(
            InstrumentExecutionSnapshot(
                uuid4(), uuid4(), "a" * 64, "USD", Decimal("100"), Decimal("0.01")
            ),
            quote,
            NOW.replace(tzinfo=None),
        )
