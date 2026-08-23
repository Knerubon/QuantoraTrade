"""Fail-closed boundary between persisted risk approval and a broker port."""

import json
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from quantora_trade.domain.enums import TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent, Decision, RiskAssessment
from quantora_trade.domain.ports import BrokerOrderResult, BrokerPort, ClockPort
from quantora_trade.risk.approval import build_approved_order_intent
from quantora_trade.risk.kill_switch import KillSwitchQuery, KillSwitchService


@dataclass(frozen=True, slots=True)
class SubmissionContext:
    """Authoritative routing context persisted with approval evidence."""

    account: str
    asset: str
    strategy: str

    def __post_init__(self) -> None:
        for name in ("account", "asset", "strategy"):
            value = getattr(self, name)
            if not value.strip() or value != value.strip():
                raise ValueError(f"{name} must be a non-empty trimmed value")
        if self.asset != self.asset.upper():
            raise ValueError("asset must be canonical uppercase")


class ApprovalEvidenceRepository(Protocol):
    """Read immutable approval evidence and its trusted routing scope."""

    def decision(self, decision_id: UUID) -> Decision | None: ...

    def assessment(self, assessment_id: UUID) -> RiskAssessment | None: ...

    def submission_context(self, assessment_id: UUID) -> SubmissionContext | None: ...


class SubmissionClaimState(StrEnum):
    ACQUIRED = "acquired"
    COMPLETED = "completed"
    IN_FLIGHT = "in_flight"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SubmissionClaim:
    state: SubmissionClaimState
    result: BrokerOrderResult | None = None

    def __post_init__(self) -> None:
        if (self.state is SubmissionClaimState.COMPLETED) != (self.result is not None):
            raise ValueError("only a completed claim may contain a broker result")


class SubmissionJournal(Protocol):
    """Durable claims prevent concurrent and crash-recovery double submission."""

    def claim(self, idempotency_key: str, request_hash: str) -> SubmissionClaim: ...

    def complete(self, idempotency_key: str, result: BrokerOrderResult) -> None: ...

    def abandon(self, idempotency_key: str) -> None: ...


class SubmissionRecoveryLookup(Protocol):
    """Deterministic broker lookup; it must never create or resubmit an order."""

    def lookup(self, idempotency_key: str, request_hash: str) -> BrokerOrderResult | None: ...


def submission_request_hash(intent: ApprovedOrderIntent) -> str:
    """Return a stable hash binding an idempotency key to the exact request."""

    payload = {
        "created_at": intent.created_at.isoformat(),
        "id": str(intent.id),
        "idempotency_key": intent.idempotency_key,
        "mode": intent.mode.value,
        "risk_assessment_id": str(intent.risk_assessment_id),
        "side": intent.side.value,
        "stop_loss": str(intent.stop_loss),
        "symbol": intent.symbol,
        "take_profit": None if intent.take_profit is None else str(intent.take_profit),
        "volume": str(intent.volume),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()


class OrderSubmissionService:
    """The sole service-level owner of BrokerPort and its external side effect."""

    def __init__(
        self,
        *,
        evidence: ApprovalEvidenceRepository,
        journal: SubmissionJournal,
        kill_switch: KillSwitchService,
        broker: BrokerPort,
        clock: ClockPort,
        decision_policy_version: str,
        risk_policy_version: str,
    ) -> None:
        if not decision_policy_version.strip() or not risk_policy_version.strip():
            raise ValueError("policy versions must not be empty")
        self._evidence = evidence
        self._journal = journal
        self._kill_switch = kill_switch
        self._broker = broker
        self._clock = clock
        self._decision_policy_version = decision_policy_version
        self._risk_policy_version = risk_policy_version

    def submit(self, intent: ApprovedOrderIntent) -> BrokerOrderResult:
        """Submit verified PAPER evidence once, or fail without automatic retry."""

        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("trusted clock must return timezone-aware UTC")
        if intent.mode is TradingMode.LIVE:
            raise PermissionError("LIVE submission is disabled in Phase 5")
        if intent.mode is not TradingMode.PAPER:
            raise PermissionError("broker submission is restricted to PAPER mode")
        if intent.created_at > now:
            raise PermissionError("order intent cannot come from the future")

        assessment = self._evidence.assessment(intent.risk_assessment_id)
        if assessment is None:
            raise PermissionError("approval evidence is missing")
        decision = self._evidence.decision(assessment.decision_id)
        context = self._evidence.submission_context(assessment.id)
        if decision is None or context is None:
            raise PermissionError("decision or routing evidence is missing")
        if decision.policy_version != self._decision_policy_version:
            raise PermissionError("decision policy version mismatch")
        if assessment.policy_version != self._risk_policy_version:
            raise PermissionError("risk policy version mismatch")
        if now >= decision.expires_at:
            raise PermissionError("decision has expired")

        expected = build_approved_order_intent(
            decision=decision,
            assessment=assessment,
            mode=intent.mode,
            created_at=intent.created_at,
        )
        if intent != expected:
            raise PermissionError("order intent does not match approval evidence")

        claim = self._journal.claim(intent.idempotency_key, submission_request_hash(intent))
        if claim.state is SubmissionClaimState.COMPLETED:
            assert claim.result is not None
            return claim.result
        if claim.state is SubmissionClaimState.IN_FLIGHT:
            raise RuntimeError("submission outcome is pending reconciliation")
        if claim.state is SubmissionClaimState.UNKNOWN:
            raise RuntimeError("submission outcome is unknown and requires reconciliation")

        query = KillSwitchQuery(
            account=context.account,
            asset=context.asset,
            symbol=intent.symbol,
            strategy=context.strategy,
            new_entry=True,
        )
        with self._kill_switch.submission_guard(query):
            if self._kill_switch.is_blocked(query):
                self._journal.abandon(intent.idempotency_key)
                raise PermissionError("kill switch blocks order submission")
            # If this raises, retain IN_FLIGHT: the broker outcome is uncertain.
            result = self._broker.submit(intent)
        self._journal.complete(intent.idempotency_key, result)
        return result
