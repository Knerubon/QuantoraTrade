"""Add durable PAPER worker runtime state.

Revision ID: 20260823_0007
Revises: 20260823_0006
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823_0007"
down_revision: str | None = "20260823_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_worker_states",
        sa.Column("id", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=True),
        sa.Column("config_hash", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_generation", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("id = 'paper'", name=op.f("ck_paper_worker_states_singleton_id")),
        sa.CheckConstraint(
            "status IN ('stopped','starting','running','stopping','degraded','halted')",
            name=op.f("ck_paper_worker_states_status_valid"),
        ),
        sa.CheckConstraint("version >= 0", name=op.f("ck_paper_worker_states_version_nonnegative")),
        sa.CheckConstraint(
            "(config IS NULL) = (config_hash IS NULL)",
            name=op.f("ck_paper_worker_states_config_hash_consistent"),
        ),
        sa.CheckConstraint(
            "last_heartbeat_at IS NULL OR last_heartbeat_at >= changed_at",
            name=op.f("ck_paper_worker_states_heartbeat_monotonic"),
        ),
        sa.CheckConstraint(
            "(active_generation IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND lease_heartbeat_at IS NULL) OR "
            "(active_generation IS NOT NULL AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND lease_heartbeat_at IS NOT NULL)",
            name=op.f("ck_paper_worker_states_lease_consistent"),
        ),
        sa.CheckConstraint(
            "lease_owner IS NULL OR length(lease_owner) > 0",
            name=op.f("ck_paper_worker_states_lease_owner_nonempty"),
        ),
        sa.CheckConstraint(
            "lease_expires_at IS NULL OR lease_expires_at > lease_heartbeat_at",
            name=op.f("ck_paper_worker_states_lease_window_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_worker_states")),
        schema="quantora",
    )
    op.create_table(
        "paper_worker_transitions",
        sa.Column("command_id", sa.String(255), nullable=False),
        sa.Column("fingerprint", sa.String(500), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(command_id) > 0",
            name=op.f("ck_paper_worker_transitions_command_id_nonempty"),
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[[:print:]]+$'",
            name=op.f("ck_paper_worker_transitions_fingerprint_nonempty"),
        ),
        sa.PrimaryKeyConstraint("command_id", name=op.f("pk_paper_worker_transitions")),
        schema="quantora",
    )
    op.execute(
        """
        CREATE FUNCTION quantora.prevent_paper_worker_transition_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'paper worker transition audit is append-only';
        END;
        $$;
        CREATE TRIGGER trg_paper_worker_transitions_append_only
        BEFORE UPDATE OR DELETE ON quantora.paper_worker_transitions
        FOR EACH ROW EXECUTE FUNCTION quantora.prevent_paper_worker_transition_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_paper_worker_transitions_append_only "
        "ON quantora.paper_worker_transitions"
    )
    op.execute("DROP FUNCTION IF EXISTS quantora.prevent_paper_worker_transition_mutation()")
    op.drop_table("paper_worker_transitions", schema="quantora")
    op.drop_table("paper_worker_states", schema="quantora")
