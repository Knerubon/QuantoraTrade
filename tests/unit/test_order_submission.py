"""Safety proofs for the Phase 5 broker-submission boundary."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Lock, Thread
from uuid import UUID, uuid4

import pytest

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent, Decision, RiskAssessment
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
    submission_request_hash,
)

NOW = datetime(2026, 8, 23, 4, 0, tzinfo=UTC)
CONTEXT = SubmissionContext(account="paper-1", asset="METAL", strategy="trend-v1")


@dataclass(frozen=True)
class Result:
    external_order_id: str


class Clock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class Broker:
    def __init__(self) -> None:
        self.call_count = 0

    def submit(self, order: ApprovedOrderIntent) -> Result:
        self.call_count += 1
        return Result(f"paper-{order.id}")


class Evidence:
    def __init__(self, decision: Decision, assessment: RiskAssessment) -> None:
        self.decisions = {decision.id: decision}
        self.assessments = {assessment.id: assessment}
        self.contexts = {assessment.id: CONTEXT}

    def decision(self, decision_id: UUID) -> Decision | None:
        return self.decisions.get(decision_id)

    def assessment(self, assessment_id: UUID) -> RiskAssessment | None:
        return self.assessments.get(assessment_id)

    def submission_context(self, assessment_id: UUID) -> SubmissionContext | None:
        return self.contexts.get(assessment_id)


class Journal:
    def __init__(self) -> None:
        self.claims: dict[str, Result | None] = {}
        self.lock = Lock()

    def claim(self, key: str, request_hash: str) -> SubmissionClaim:
        with self.lock:
            assert len(request_hash) == 64
            if key not in self.claims:
                self.claims[key] = None
                return SubmissionClaim(SubmissionClaimState.ACQUIRED)
            result = self.claims[key]
            if result is None:
                return SubmissionClaim(SubmissionClaimState.IN_FLIGHT)
            return SubmissionClaim(SubmissionClaimState.COMPLETED, result)

    def complete(self, key: str, result: Result) -> None:
        with self.lock:
            if key not in self.claims or self.claims[key] is not None:
                raise ValueError("claim is not available for completion")
            self.claims[key] = result

    def abandon(self, key: str) -> None:
        with self.lock:
            if self.claims.get(key) is None:
                self.claims.pop(key, None)


def approved_evidence() -> tuple[Decision, RiskAssessment]:
    decision = Decision(
        id=uuid4(),
        signal_id=uuid4(),
        symbol="XAUUSD",
        timeframe="M15",
        action=Action.BUY,
        confidence=Decimal("0.8"),
        policy_version="decision-v1",
        reason_codes=("H1_BULLISH_CONTEXT",),
        expires_at=NOW + timedelta(minutes=15),
    )
    assessment = RiskAssessment(
        id=uuid4(),
        decision_id=decision.id,
        policy_version="risk-v1",
        approved=True,
        rejection_codes=(),
        risk_amount=Decimal("100"),
        volume=Decimal("1"),
        stop_loss=Decimal("2399"),
        take_profit=Decimal("2402"),
        created_at=NOW,
    )
    return decision, assessment


def setup(
    *, clock: Clock | None = None, journal: Journal | None = None
) -> tuple[OrderSubmissionService, Broker, Evidence, KillSwitchService, Clock, Journal]:
    decision, assessment = approved_evidence()
    broker, evidence = Broker(), Evidence(decision, assessment)
    kill_switch = KillSwitchService(InMemoryKillSwitchRepository())
    trusted_clock, durable_journal = clock or Clock(), journal or Journal()
    service = OrderSubmissionService(
        evidence=evidence,
        journal=durable_journal,
        kill_switch=kill_switch,
        broker=broker,
        clock=trusted_clock,
        decision_policy_version="decision-v1",
        risk_policy_version="risk-v1",
    )
    return service, broker, evidence, kill_switch, trusted_clock, durable_journal


def intent(evidence: Evidence, mode: TradingMode = TradingMode.PAPER) -> ApprovedOrderIntent:
    return build_approved_order_intent(
        decision=next(iter(evidence.decisions.values())),
        assessment=next(iter(evidence.assessments.values())),
        mode=mode,
        created_at=NOW,
    )


def test_verified_submission_and_exact_replay_are_idempotent() -> None:
    service, broker, evidence, _, clock, _ = setup()
    order = intent(evidence)
    first = service.submit(order)
    clock.value += timedelta(seconds=1)
    replay = service.submit(order)
    assert first is replay
    assert broker.call_count == 1


def test_request_hash_is_stable_and_binds_the_exact_intent() -> None:
    _, _, evidence, _, _, _ = setup()
    order = intent(evidence)
    assert submission_request_hash(order) == submission_request_hash(order)
    assert len(submission_request_hash(order)) == 64
    assert submission_request_hash(replace(order, volume=Decimal("2"))) != submission_request_hash(
        order
    )


@pytest.mark.parametrize(
    "forge",
    [
        lambda order: replace(order, volume=Decimal("2")),
        lambda order: replace(order, symbol="EURUSD"),
        lambda order: replace(order, idempotency_key="quantora:forged"),
        lambda order: replace(order, risk_assessment_id=uuid4()),
    ],
)
def test_forged_intents_never_reach_broker(forge: object) -> None:
    service, broker, evidence, _, _, _ = setup()
    with pytest.raises(PermissionError):
        service.submit(forge(intent(evidence)))  # type: ignore[operator]
    assert broker.call_count == 0


def test_trusted_clock_blocks_expired_and_future_intents() -> None:
    service, broker, evidence, _, clock, _ = setup()
    order = intent(evidence)
    clock.value = NOW + timedelta(minutes=15)
    with pytest.raises(PermissionError, match="expired"):
        service.submit(order)
    clock.value = NOW - timedelta(seconds=1)
    with pytest.raises(PermissionError, match="future"):
        service.submit(order)
    assert broker.call_count == 0


def test_invalid_trusted_clock_fails_closed() -> None:
    service, broker, evidence, _, clock, _ = setup()
    clock.value = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="trusted clock"):
        service.submit(intent(evidence))
    assert broker.call_count == 0


def test_policy_mode_and_missing_authoritative_context_fail_closed() -> None:
    service, broker, evidence, _, _, _ = setup()
    order = intent(evidence)
    assessment = next(iter(evidence.assessments.values()))
    evidence.assessments[assessment.id] = replace(assessment, policy_version="risk-v2")
    with pytest.raises(PermissionError, match="policy version"):
        service.submit(order)
    evidence.assessments[assessment.id] = assessment
    evidence.contexts.clear()
    with pytest.raises(PermissionError, match="routing evidence"):
        service.submit(order)
    for mode in (TradingMode.LIVE, TradingMode.BACKTEST):
        with pytest.raises(PermissionError):
            service.submit(intent(evidence, mode))
    assert broker.call_count == 0


@pytest.mark.parametrize(
    "scope",
    [
        KillSwitchScope(KillSwitchScopeKind.ACCOUNT, "paper-1"),
        KillSwitchScope(KillSwitchScopeKind.ASSET, "METAL"),
        KillSwitchScope(KillSwitchScopeKind.SYMBOL, "XAUUSD"),
        KillSwitchScope(KillSwitchScopeKind.STRATEGY, "trend-v1"),
    ],
)
def test_every_authoritative_scope_blocks_submission(scope: KillSwitchScope) -> None:
    service, broker, evidence, kill_switch, _, _ = setup()
    kill_switch.activate(
        scope, occurred_at=NOW, actor="risk", reason="incident", incident_reference="INC-1"
    )
    with pytest.raises(PermissionError, match="kill switch"):
        service.submit(intent(evidence))
    assert broker.call_count == 0


def test_atomic_claim_blocks_concurrent_or_crash_retry() -> None:
    service, broker, evidence, _, _, journal = setup()
    order = intent(evidence)
    assert (
        journal.claim(order.idempotency_key, submission_request_hash(order)).state
        is SubmissionClaimState.ACQUIRED
    )
    with pytest.raises(RuntimeError, match="reconciliation"):
        service.submit(order)
    assert broker.call_count == 0


def test_two_concurrent_calls_cannot_both_reach_broker() -> None:
    service, broker, evidence, _, _, _ = setup()
    order, barrier, outcomes = intent(evidence), Barrier(3), []

    def run() -> None:
        barrier.wait()
        try:
            outcomes.append(service.submit(order))
        except RuntimeError:
            outcomes.append("in-flight")

    threads = [Thread(target=run), Thread(target=run)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert broker.call_count == 1
    assert len(outcomes) == 2


def test_context_and_scope_reject_noncanonical_values() -> None:
    with pytest.raises(ValueError, match="trimmed"):
        SubmissionContext(account=" paper-1", asset="METAL", strategy="trend-v1")
    with pytest.raises(ValueError, match="uppercase"):
        SubmissionContext(account="paper-1", asset="metal", strategy="trend-v1")
    with pytest.raises(ValueError, match="canonical uppercase"):
        KillSwitchScope(KillSwitchScopeKind.SYMBOL, "xauusd")
