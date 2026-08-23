"""PostgreSQL approval-evidence and submission-journal adapters."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quantora_trade.domain.enums import Action
from quantora_trade.domain.models import Decision, RiskAssessment
from quantora_trade.domain.ports import BrokerOrderResult
from quantora_trade.infrastructure.database.execution_models import (
    DecisionEvidenceModel,
    RiskAssessmentEvidenceModel,
    SubmissionJournalModel,
)
from quantora_trade.risk.submission import (
    SubmissionClaim,
    SubmissionClaimState,
    SubmissionContext,
    SubmissionRecoveryLookup,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DurableBrokerOrderResult:
    """Minimal broker result restored after a process restart."""

    external_order_id: str
    metadata: Mapping[str, object] | None = None


class PostgresApprovalEvidenceRepository:
    """Persist once and read authoritative, immutable approval evidence."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def persist(
        self, decision: Decision, assessment: RiskAssessment, context: SubmissionContext
    ) -> None:
        if assessment.decision_id != decision.id:
            raise ValueError("assessment must reference decision")
        with self._session_factory() as session, session.begin():
            decision_row = self._decision_row(decision)
            session.execute(
                insert(DecisionEvidenceModel)
                .values(
                    id=decision_row.id,
                    signal_id=decision_row.signal_id,
                    symbol=decision_row.symbol,
                    timeframe=decision_row.timeframe,
                    action=decision_row.action,
                    confidence=decision_row.confidence,
                    policy_version=decision_row.policy_version,
                    reason_codes=decision_row.reason_codes,
                    expires_at=decision_row.expires_at,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            current_decision = session.get(DecisionEvidenceModel, decision.id)
            if current_decision is None or self._decision(current_decision) != decision:
                raise ValueError("decision evidence id collision")

            assessment_row = self._assessment_row(assessment, context)
            session.execute(
                insert(RiskAssessmentEvidenceModel)
                .values(
                    id=assessment_row.id,
                    decision_id=assessment_row.decision_id,
                    policy_version=assessment_row.policy_version,
                    approved=assessment_row.approved,
                    rejection_codes=assessment_row.rejection_codes,
                    risk_amount=assessment_row.risk_amount,
                    volume=assessment_row.volume,
                    stop_loss=assessment_row.stop_loss,
                    take_profit=assessment_row.take_profit,
                    created_at=assessment_row.created_at,
                    account=assessment_row.account,
                    asset=assessment_row.asset,
                    strategy=assessment_row.strategy,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            current_assessment = session.get(RiskAssessmentEvidenceModel, assessment.id)
            if current_assessment is None or (
                self._assessment(current_assessment) != assessment
                or self._context(current_assessment) != context
            ):
                raise ValueError("risk assessment evidence id collision")

    def decision(self, decision_id: UUID) -> Decision | None:
        with self._session_factory() as session:
            row = session.get(DecisionEvidenceModel, decision_id)
            return None if row is None else self._decision(row)

    def assessment(self, assessment_id: UUID) -> RiskAssessment | None:
        with self._session_factory() as session:
            row = session.get(RiskAssessmentEvidenceModel, assessment_id)
            return None if row is None else self._assessment(row)

    def submission_context(self, assessment_id: UUID) -> SubmissionContext | None:
        with self._session_factory() as session:
            row = session.get(RiskAssessmentEvidenceModel, assessment_id)
            return None if row is None else self._context(row)

    @staticmethod
    def _decision_row(value: Decision) -> DecisionEvidenceModel:
        return DecisionEvidenceModel(
            id=value.id,
            signal_id=value.signal_id,
            symbol=value.symbol,
            timeframe=value.timeframe,
            action=value.action.value,
            confidence=value.confidence,
            policy_version=value.policy_version,
            reason_codes=list(value.reason_codes),
            expires_at=value.expires_at,
        )

    @staticmethod
    def _assessment_row(
        value: RiskAssessment, context: SubmissionContext
    ) -> RiskAssessmentEvidenceModel:
        return RiskAssessmentEvidenceModel(
            id=value.id,
            decision_id=value.decision_id,
            policy_version=value.policy_version,
            approved=value.approved,
            rejection_codes=list(value.rejection_codes),
            risk_amount=value.risk_amount,
            volume=value.volume,
            stop_loss=value.stop_loss,
            take_profit=value.take_profit,
            created_at=value.created_at,
            account=context.account,
            asset=context.asset,
            strategy=context.strategy,
        )

    @staticmethod
    def _decision(row: DecisionEvidenceModel) -> Decision:
        return Decision(
            id=row.id,
            signal_id=row.signal_id,
            symbol=row.symbol,
            timeframe=row.timeframe,
            action=Action(row.action),
            confidence=row.confidence,
            policy_version=row.policy_version,
            reason_codes=tuple(row.reason_codes),
            expires_at=_utc(row.expires_at),
        )

    @staticmethod
    def _assessment(row: RiskAssessmentEvidenceModel) -> RiskAssessment:
        return RiskAssessment(
            id=row.id,
            decision_id=row.decision_id,
            policy_version=row.policy_version,
            approved=row.approved,
            rejection_codes=tuple(row.rejection_codes),
            risk_amount=row.risk_amount,
            volume=row.volume,
            stop_loss=row.stop_loss,
            take_profit=row.take_profit,
            created_at=_utc(row.created_at),
        )

    @staticmethod
    def _context(row: RiskAssessmentEvidenceModel) -> SubmissionContext:
        return SubmissionContext(account=row.account, asset=row.asset, strategy=row.strategy)


class PostgresSubmissionJournal:
    """Atomic durable claim journal; uncertain outcomes require explicit reconciliation."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_duration: timedelta = timedelta(minutes=2),
    ) -> None:
        self._session_factory = session_factory
        self._now = now
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self._lease_duration = lease_duration
        self._owners: dict[str, UUID] = {}
        self._owners_lock = Lock()

    def claim(self, idempotency_key: str, request_hash: str) -> SubmissionClaim:
        if not idempotency_key.strip() or re.fullmatch(r"[0-9a-f]{64}", request_hash) is None:
            raise ValueError("valid idempotency key and request hash are required")
        now = self._now()
        owner = uuid4()
        with self._session_factory() as session, session.begin():
            statement = (
                insert(SubmissionJournalModel)
                .values(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    claim_owner=owner,
                    lease_expires_at=now + self._lease_duration,
                    fencing_token=1,
                    state="in_flight",
                    claimed_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(SubmissionJournalModel.idempotency_key)
            )
            acquired = session.scalar(statement) is not None
            row = session.scalar(
                select(SubmissionJournalModel)
                .where(SubmissionJournalModel.idempotency_key == idempotency_key)
                .with_for_update()
            )
            if row is None:
                raise RuntimeError("submission claim disappeared")
            if row.request_hash != request_hash:
                raise ValueError("idempotency key is already bound to another request")
            if acquired:
                with self._owners_lock:
                    self._owners[idempotency_key] = owner
                return SubmissionClaim(SubmissionClaimState.ACQUIRED)
            if row.state == "completed":
                assert row.external_order_id is not None
                result = DurableBrokerOrderResult(row.external_order_id, row.result_metadata)
                return SubmissionClaim(SubmissionClaimState.COMPLETED, result)
            return SubmissionClaim(SubmissionClaimState(row.state))

    def complete(self, idempotency_key: str, result: BrokerOrderResult) -> None:
        owner = self._owned(idempotency_key)
        with self._session_factory() as session, session.begin():
            row = self._locked(session, idempotency_key)
            if (
                row.state != "in_flight"
                or row.claim_owner != owner
                or _utc(row.lease_expires_at) <= _utc(self._now())
            ):
                raise ValueError("only the claim owner can complete an in-flight claim")
            if not result.external_order_id.strip():
                raise ValueError("external order id must not be empty")
            row.state = "completed"
            row.external_order_id = result.external_order_id
            row.result_metadata = {"external_order_id": result.external_order_id}
            row.updated_at = self._now()
        self._forget(idempotency_key, owner)

    def mark_unknown(
        self, idempotency_key: str, *, recovery_metadata: Mapping[str, object]
    ) -> None:
        if not recovery_metadata:
            raise ValueError("recovery metadata is required")
        owner = self._owned(idempotency_key)
        with self._session_factory() as session, session.begin():
            row = self._locked(session, idempotency_key)
            if (
                row.state != "in_flight"
                or row.claim_owner != owner
                or _utc(row.lease_expires_at) <= _utc(self._now())
            ):
                raise ValueError("only the claim owner can mark an in-flight claim unknown")
            row.state = "unknown"
            row.recovery_metadata = dict(recovery_metadata)
            row.updated_at = self._now()
        self._forget(idempotency_key, owner)

    def recover_expired(
        self,
        idempotency_key: str,
        request_hash: str,
        *,
        lookup: SubmissionRecoveryLookup,
        recovery_metadata: Mapping[str, object],
    ) -> SubmissionClaim:
        """Fence an expired owner, then reconcile by lookup without resubmission."""

        if not recovery_metadata:
            raise ValueError("recovery metadata is required")
        now = _utc(self._now())
        owner = uuid4()
        with self._session_factory() as session, session.begin():
            row = self._locked(session, idempotency_key)
            if row.request_hash != request_hash:
                raise ValueError("idempotency key is already bound to another request")
            if row.state == "completed":
                assert row.external_order_id is not None
                return SubmissionClaim(
                    SubmissionClaimState.COMPLETED,
                    DurableBrokerOrderResult(row.external_order_id, row.result_metadata),
                )
            if row.state == "unknown":
                return SubmissionClaim(SubmissionClaimState.UNKNOWN)
            if _utc(row.lease_expires_at) > now:
                return SubmissionClaim(SubmissionClaimState.IN_FLIGHT)
            row.claim_owner = owner
            row.fencing_token += 1
            row.lease_expires_at = now + self._lease_duration
            row.updated_at = now
        with self._owners_lock:
            self._owners[idempotency_key] = owner
        result = lookup.lookup(idempotency_key, request_hash)
        if result is None:
            self.mark_unknown(idempotency_key, recovery_metadata=recovery_metadata)
            return SubmissionClaim(SubmissionClaimState.UNKNOWN)
        self.complete(idempotency_key, result)
        return SubmissionClaim(SubmissionClaimState.COMPLETED, result)

    def reconcile_completed(
        self,
        idempotency_key: str,
        result: BrokerOrderResult,
        *,
        recovery_metadata: Mapping[str, object],
    ) -> None:
        if not recovery_metadata or not result.external_order_id.strip():
            raise ValueError("result and recovery metadata are required")
        with self._session_factory() as session, session.begin():
            row = self._locked(session, idempotency_key)
            if row.state != "unknown":
                raise ValueError("only an unknown claim can be reconciled")
            row.state = "completed"
            row.external_order_id = result.external_order_id
            row.result_metadata = {"external_order_id": result.external_order_id}
            row.recovery_metadata = dict(recovery_metadata)
            row.updated_at = self._now()

    def abandon(self, idempotency_key: str) -> None:
        owner = self._owned(idempotency_key)
        with self._session_factory() as session, session.begin():
            statement = (
                delete(SubmissionJournalModel)
                .where(
                    SubmissionJournalModel.idempotency_key == idempotency_key,
                    SubmissionJournalModel.state == "in_flight",
                    SubmissionJournalModel.claim_owner == owner,
                    SubmissionJournalModel.lease_expires_at > self._now(),
                )
                .returning(SubmissionJournalModel.idempotency_key)
            )
            if session.scalar(statement) is None:
                raise ValueError("only the claim owner can abandon an in-flight claim")
        self._forget(idempotency_key, owner)

    def _owned(self, idempotency_key: str) -> UUID:
        with self._owners_lock:
            owner = self._owners.get(idempotency_key)
        if owner is None:
            raise ValueError("submission claim is not owned by this journal instance")
        return owner

    def _forget(self, idempotency_key: str, owner: UUID) -> None:
        with self._owners_lock:
            if self._owners.get(idempotency_key) == owner:
                del self._owners[idempotency_key]

    @staticmethod
    def _locked(session: Session, idempotency_key: str) -> SubmissionJournalModel:
        row = session.scalar(
            select(SubmissionJournalModel)
            .where(SubmissionJournalModel.idempotency_key == idempotency_key)
            .with_for_update()
        )
        if row is None:
            raise ValueError("submission claim does not exist")
        return row


__all__ = [
    "DurableBrokerOrderResult",
    "PostgresApprovalEvidenceRepository",
    "PostgresSubmissionJournal",
]
