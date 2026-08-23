"""Add immutable approval evidence and durable submission journal.

Revision ID: 20260823_0004
Revises: 20260823_0003
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823_0004"
down_revision: str | None = "20260823_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Numeric(20, 18), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decision_evidence")),
        schema="quantora",
    )
    op.create_table(
        "risk_assessment_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("rejection_codes", postgresql.JSONB(), nullable=False),
        sa.Column("risk_amount", sa.Numeric(30, 12), nullable=False),
        sa.Column("volume", sa.Numeric(30, 12), nullable=False),
        sa.Column("stop_loss", sa.Numeric(30, 12), nullable=True),
        sa.Column("take_profit", sa.Numeric(30, 12), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account", sa.String(255), nullable=False),
        sa.Column("asset", sa.String(50), nullable=False),
        sa.Column("strategy", sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["quantora.decision_evidence.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_assessment_evidence")),
        schema="quantora",
    )
    op.create_table(
        "submission_journal",
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("claim_owner", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("external_order_id", sa.String(255), nullable=True),
        sa.Column("result_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("recovery_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('in_flight', 'completed', 'unknown')",
            name=op.f("ck_submission_journal_state_valid"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_submission_journal_request_hash_valid"),
        ),
        sa.CheckConstraint("fencing_token > 0", name=op.f("ck_submission_journal_fence_positive")),
        sa.CheckConstraint(
            "(state = 'completed' AND external_order_id IS NOT NULL) OR "
            "(state <> 'completed' AND external_order_id IS NULL)",
            name=op.f("ck_submission_journal_completed_result_valid"),
        ),
        sa.PrimaryKeyConstraint("idempotency_key", name=op.f("pk_submission_journal")),
        schema="quantora",
    )
    op.execute(
        """
        CREATE FUNCTION quantora.reject_approval_evidence_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'approval evidence is append-only' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table in ("decision_evidence", "risk_assessment_evidence"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON quantora.{table}
            FOR EACH ROW EXECUTE FUNCTION quantora.reject_approval_evidence_mutation()
            """
        )
    op.execute(
        """
        CREATE FUNCTION quantora.validate_submission_journal_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.idempotency_key IS DISTINCT FROM NEW.idempotency_key
                OR OLD.request_hash IS DISTINCT FROM NEW.request_hash
                OR OLD.claimed_at IS DISTINCT FROM NEW.claimed_at THEN
                RAISE EXCEPTION 'submission identity is immutable' USING ERRCODE = '55000';
            END IF;
            IF NEW.fencing_token < OLD.fencing_token
                OR (NEW.claim_owner IS DISTINCT FROM OLD.claim_owner
                    AND NEW.fencing_token <> OLD.fencing_token + 1) THEN
                RAISE EXCEPTION 'invalid submission fencing transition' USING ERRCODE = '55000';
            END IF;
            IF OLD.state = 'completed'
                OR (OLD.state = 'unknown' AND NEW.state <> 'completed')
                OR (OLD.state = 'in_flight' AND NEW.state NOT IN ('unknown', 'completed')) THEN
                RAISE EXCEPTION 'invalid submission state transition' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_submission_journal_transition
        BEFORE UPDATE ON quantora.submission_journal
        FOR EACH ROW EXECUTE FUNCTION quantora.validate_submission_journal_transition()
        """
    )
    op.execute(
        """
        CREATE FUNCTION quantora.validate_submission_journal_delete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.state <> 'in_flight' THEN
                RAISE EXCEPTION 'only an owned in-flight claim may be abandoned'
                    USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_submission_journal_delete
        BEFORE DELETE ON quantora.submission_journal
        FOR EACH ROW EXECUTE FUNCTION quantora.validate_submission_journal_delete()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_submission_journal_delete ON quantora.submission_journal"
    )
    op.execute("DROP FUNCTION IF EXISTS quantora.validate_submission_journal_delete()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_submission_journal_transition ON quantora.submission_journal"
    )
    op.execute("DROP FUNCTION IF EXISTS quantora.validate_submission_journal_transition()")
    for table in ("risk_assessment_evidence", "decision_evidence"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON quantora.{table}")
    op.execute("DROP FUNCTION IF EXISTS quantora.reject_approval_evidence_mutation()")
    op.drop_table("submission_journal", schema="quantora")
    op.drop_table("risk_assessment_evidence", schema="quantora")
    op.drop_table("decision_evidence", schema="quantora")
