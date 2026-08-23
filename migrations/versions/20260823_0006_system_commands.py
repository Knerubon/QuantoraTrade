"""Add the durable PAPER control-command queue.

Revision ID: 20260823_0006
Revises: 20260823_0005
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823_0006"
down_revision: str | None = "20260823_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("queue_sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("request_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('start', 'stop')", name=op.f("ck_system_commands_action_valid")
        ),
        sa.CheckConstraint("mode = 'paper'", name=op.f("ck_system_commands_paper_mode_only")),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed')",
            name=op.f("ck_system_commands_status_valid"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_system_commands_request_hash_valid"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_system_commands_attempts_nonnegative")),
        sa.CheckConstraint(
            "updated_at >= created_at", name=op.f("ck_system_commands_timestamps_monotonic")
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND worker_id IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL AND completed_at IS NULL AND result IS NULL) OR "
            "(status = 'processing' AND worker_id IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND completed_at IS NULL AND result IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND worker_id IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND result IS NOT NULL)",
            name=op.f("ck_system_commands_state_consistent"),
        ),
        sa.CheckConstraint(
            "last_heartbeat_at IS NULL OR last_heartbeat_at >= created_at",
            name=op.f("ck_system_commands_heartbeat_monotonic"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name=op.f("ck_system_commands_completion_monotonic"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_commands")),
        sa.UniqueConstraint("queue_sequence", name=op.f("uq_system_commands_queue_sequence")),
        sa.UniqueConstraint(
            "actor", "idempotency_key", name=op.f("uq_system_commands_actor_idempotency_key_unique")
        ),
        schema="quantora",
    )
    op.create_index(
        "ix_system_commands_queue",
        "system_commands",
        ["status", "queue_sequence"],
        unique=False,
        schema="quantora",
    )
    op.execute(
        """
        CREATE FUNCTION quantora.prevent_system_command_audit_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.queue_sequence IS DISTINCT FROM OLD.queue_sequence
             OR NEW.request_id IS DISTINCT FROM OLD.request_id
             OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
             OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
             OR NEW.action IS DISTINCT FROM OLD.action
             OR NEW.mode IS DISTINCT FROM OLD.mode
             OR NEW.payload IS DISTINCT FROM OLD.payload
             OR NEW.actor IS DISTINCT FROM OLD.actor
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'system command audit identity and payload are immutable';
          END IF;
          IF (OLD.status = 'queued' AND NEW.status NOT IN ('queued', 'processing'))
             OR (OLD.status = 'processing'
                 AND NEW.status NOT IN ('processing', 'succeeded', 'failed'))
             OR (OLD.status IN ('succeeded', 'failed') AND NEW IS DISTINCT FROM OLD) THEN
            RAISE EXCEPTION 'illegal system command state transition';
          END IF;
          IF NEW.updated_at < OLD.updated_at OR NEW.attempts < OLD.attempts THEN
            RAISE EXCEPTION 'system command audit timestamps and attempts are monotonic';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_system_commands_immutable_audit
        BEFORE UPDATE ON quantora.system_commands
        FOR EACH ROW EXECUTE FUNCTION quantora.prevent_system_command_audit_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_system_commands_immutable_audit ON quantora.system_commands"
    )
    op.execute("DROP FUNCTION IF EXISTS quantora.prevent_system_command_audit_mutation()")
    op.drop_index("ix_system_commands_queue", table_name="system_commands", schema="quantora")
    op.drop_table("system_commands", schema="quantora")
