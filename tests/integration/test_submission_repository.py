"""PostgreSQL integration tests for approval evidence and submission claims."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from quantora_trade.domain.enums import Action
from quantora_trade.domain.models import Decision, RiskAssessment
from quantora_trade.infrastructure.database.submission_repository import (
    PostgresApprovalEvidenceRepository,
    PostgresSubmissionJournal,
)
from quantora_trade.risk.submission import SubmissionClaimState, SubmissionContext

DATABASE_URL = os.getenv("QUANTORA_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("QUANTORA_DATABASE_URL is required for integration tests", allow_module_level=True)

engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(engine, expire_on_commit=False)
NOW = datetime(2026, 8, 23, 6, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Result:
    external_order_id: str


class Lookup:
    def __init__(self, result: Result | None) -> None:
        self.result = result
        self.calls = 0

    def lookup(self, idempotency_key: str, request_hash: str) -> Result | None:
        assert idempotency_key and len(request_hash) == 64
        self.calls += 1
        return self.result


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


@pytest.fixture(autouse=True)
def clean_execution_tables() -> None:
    with SessionFactory() as session, session.begin():
        session.execute(
            text(
                "TRUNCATE quantora.submission_journal, quantora.risk_assessment_evidence, "
                "quantora.decision_evidence"
            )
        )


def evidence() -> tuple[Decision, RiskAssessment, SubmissionContext]:
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
    return decision, assessment, SubmissionContext("paper-1", "METAL", "trend-v1")


def test_evidence_survives_restart_and_cannot_be_changed() -> None:
    decision, assessment, context = evidence()
    PostgresApprovalEvidenceRepository(SessionFactory).persist(decision, assessment, context)
    restarted = PostgresApprovalEvidenceRepository(SessionFactory)
    assert restarted.decision(decision.id) == decision
    assert restarted.assessment(assessment.id) == assessment
    assert restarted.submission_context(assessment.id) == context
    restarted.persist(decision, assessment, context)
    with pytest.raises(ValueError, match="collision"):
        restarted.persist(replace(decision, confidence=Decimal("0.7")), assessment, context)
    with (
        SessionFactory() as session,
        session.begin(),
        pytest.raises(DBAPIError, match="append-only"),
    ):
        session.execute(
            text("UPDATE quantora.decision_evidence SET confidence = 0.1 WHERE id = :id"),
            {"id": decision.id},
        )


def test_claim_completion_and_restart_replay_are_durable() -> None:
    journal = PostgresSubmissionJournal(SessionFactory, now=lambda: NOW)
    assert journal.claim("key-1", "a" * 64).state is SubmissionClaimState.ACQUIRED
    assert journal.claim("key-1", "a" * 64).state is SubmissionClaimState.IN_FLIGHT
    journal.complete("key-1", Result("paper-42"))
    replay = PostgresSubmissionJournal(SessionFactory).claim("key-1", "a" * 64)
    assert replay.state is SubmissionClaimState.COMPLETED
    assert replay.result is not None
    assert replay.result.external_order_id == "paper-42"
    with pytest.raises(ValueError, match="another request"):
        journal.claim("key-1", "b" * 64)


def test_unknown_requires_explicit_reconciliation_and_never_reacquires() -> None:
    journal = PostgresSubmissionJournal(SessionFactory, now=lambda: NOW)
    journal.claim("key-unknown", "c" * 64)
    journal.mark_unknown("key-unknown", recovery_metadata={"incident": "INC-1"})
    assert journal.claim("key-unknown", "c" * 64).state is SubmissionClaimState.UNKNOWN
    journal.reconcile_completed(
        "key-unknown", Result("paper-43"), recovery_metadata={"operator": "risk"}
    )
    assert journal.claim("key-unknown", "c" * 64).state is SubmissionClaimState.COMPLETED


def test_expired_submission_is_fenced_and_recovered_by_lookup_without_resubmit() -> None:
    clock = MutableClock(NOW)
    owner = PostgresSubmissionJournal(
        SessionFactory, now=clock, lease_duration=timedelta(seconds=10)
    )
    assert owner.claim("crash", "f" * 64).state is SubmissionClaimState.ACQUIRED
    clock.advance(timedelta(seconds=11))
    lookup = Lookup(Result("paper-recovered"))
    recovered = PostgresSubmissionJournal(SessionFactory, now=clock).recover_expired(
        "crash", "f" * 64, lookup=lookup, recovery_metadata={"worker": "recovery-1"}
    )
    assert recovered.state is SubmissionClaimState.COMPLETED
    assert lookup.calls == 1
    with pytest.raises(ValueError, match="claim owner"):
        owner.complete("crash", Result("duplicate"))


def test_expired_submission_without_lookup_result_becomes_unknown() -> None:
    clock = MutableClock(NOW)
    PostgresSubmissionJournal(SessionFactory, now=clock, lease_duration=timedelta(seconds=1)).claim(
        "missing", "1" * 64
    )
    clock.advance(timedelta(seconds=2))
    recovered = PostgresSubmissionJournal(SessionFactory, now=clock).recover_expired(
        "missing", "1" * 64, lookup=Lookup(None), recovery_metadata={"incident": "INC-2"}
    )
    assert recovered.state is SubmissionClaimState.UNKNOWN


def test_concurrent_claim_has_exactly_one_owner() -> None:
    barrier = Barrier(2)

    def claim() -> SubmissionClaimState:
        barrier.wait()
        return PostgresSubmissionJournal(SessionFactory).claim("race", "d" * 64).state

    with ThreadPoolExecutor(max_workers=2) as executor:
        states = tuple(executor.map(lambda _: claim(), range(2)))
    assert sorted(states) == [SubmissionClaimState.ACQUIRED, SubmissionClaimState.IN_FLIGHT]


def test_non_owner_cannot_abandon_or_complete_claim() -> None:
    owner = PostgresSubmissionJournal(SessionFactory, now=lambda: NOW)
    observer = PostgresSubmissionJournal(SessionFactory, now=lambda: NOW)
    assert owner.claim("owned", "e" * 64).state is SubmissionClaimState.ACQUIRED
    assert observer.claim("owned", "e" * 64).state is SubmissionClaimState.IN_FLIGHT
    with pytest.raises(ValueError, match="not owned"):
        observer.abandon("owned")
    with pytest.raises(ValueError, match="not owned"):
        observer.complete("owned", Result("paper-wrong"))
    owner.abandon("owned")
    assert observer.claim("owned", "e" * 64).state is SubmissionClaimState.ACQUIRED


def test_concurrent_identical_approval_evidence_is_idempotent() -> None:
    decision, assessment, context = evidence()
    barrier = Barrier(2)

    def persist() -> None:
        barrier.wait()
        PostgresApprovalEvidenceRepository(SessionFactory).persist(decision, assessment, context)

    with ThreadPoolExecutor(max_workers=2) as executor:
        tuple(executor.map(lambda _: persist(), range(2)))
    restarted = PostgresApprovalEvidenceRepository(SessionFactory)
    assert restarted.decision(decision.id) == decision
    assert restarted.assessment(assessment.id) == assessment


def test_submission_hash_must_be_lowercase_sha256_hex() -> None:
    journal = PostgresSubmissionJournal(SessionFactory)
    with pytest.raises(ValueError, match="valid idempotency"):
        journal.claim("bad", "g" * 64)
    with pytest.raises(ValueError, match="valid idempotency"):
        journal.claim("bad", "A" * 64)
