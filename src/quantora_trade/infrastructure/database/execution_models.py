"""SQLAlchemy models for immutable approval evidence and durable submission claims."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from quantora_trade.infrastructure.database.models import Base


class DecisionEvidenceModel(Base):
    __tablename__ = "decision_evidence"
    __table_args__ = ({"schema": "quantora"},)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    signal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(20, 18), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskAssessmentEvidenceModel(Base):
    __tablename__ = "risk_assessment_evidence"
    __table_args__ = ({"schema": "quantora"},)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quantora.decision_evidence.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rejection_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    risk_amount: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    account: Mapped[str] = mapped_column(String(255), nullable=False)
    asset: Mapped[str] = mapped_column(String(50), nullable=False)
    strategy: Mapped[str] = mapped_column(String(255), nullable=False)


class SubmissionJournalModel(Base):
    __tablename__ = "submission_journal"
    __table_args__ = (
        CheckConstraint("state IN ('in_flight', 'completed', 'unknown')", name="state_valid"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash_valid"),
        CheckConstraint(
            "(state = 'completed' AND external_order_id IS NOT NULL) OR "
            "(state <> 'completed' AND external_order_id IS NULL)",
            name="completed_result_valid",
        ),
        {"schema": "quantora"},
    )

    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_owner: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fencing_token: Mapped[int] = mapped_column(nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    external_order_id: Mapped[str | None] = mapped_column(String(255))
    result_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    recovery_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "DecisionEvidenceModel",
    "RiskAssessmentEvidenceModel",
    "SubmissionJournalModel",
]
