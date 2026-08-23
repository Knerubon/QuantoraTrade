"""Add durable kill-switch event log and current state.

Revision ID: 20260823_0003
Revises: 20260816_0002
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823_0003"
down_revision: str | None = "20260816_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kill_switch_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("scope_key", sa.String(length=300), nullable=False),
        sa.Column("scope_kind", sa.String(length=30), nullable=False),
        sa.Column("scope_value", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("incident_reference", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "action IN ('activate', 'deactivate')",
            name=op.f("ck_kill_switch_events_action_valid"),
        ),
        sa.CheckConstraint(
            "scope_kind IN ('global', 'account', 'asset', 'symbol', 'strategy', 'new_entries')",
            name=op.f("ck_kill_switch_events_scope_kind_valid"),
        ),
        sa.CheckConstraint(
            "(scope_kind IN ('global', 'new_entries') AND scope_value IS NULL) "
            "OR (scope_kind NOT IN ('global', 'new_entries') AND scope_value IS NOT NULL)",
            name=op.f("ck_kill_switch_events_scope_value_valid"),
        ),
        sa.CheckConstraint(
            "scope_key = scope_kind || ':' || COALESCE(scope_value, '*')",
            name=op.f("ck_kill_switch_events_scope_key_consistent"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kill_switch_events")),
        schema="quantora",
    )
    op.create_table(
        "kill_switch_states",
        sa.Column("scope_key", sa.String(length=300), nullable=False),
        sa.Column("scope_kind", sa.String(length=30), nullable=False),
        sa.Column("scope_value", sa.String(length=255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_transition_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_kind IN ('global', 'account', 'asset', 'symbol', 'strategy', 'new_entries')",
            name=op.f("ck_kill_switch_states_scope_kind_valid"),
        ),
        sa.CheckConstraint(
            "(scope_kind IN ('global', 'new_entries') AND scope_value IS NULL) "
            "OR (scope_kind NOT IN ('global', 'new_entries') AND scope_value IS NOT NULL)",
            name=op.f("ck_kill_switch_states_scope_value_valid"),
        ),
        sa.CheckConstraint(
            "scope_key = scope_kind || ':' || COALESCE(scope_value, '*')",
            name=op.f("ck_kill_switch_states_scope_key_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["last_event_id"],
            ["quantora.kill_switch_events.id"],
            name=op.f("fk_kill_switch_states_last_event_id_kill_switch_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("scope_key", name=op.f("pk_kill_switch_states")),
        sa.UniqueConstraint(
            "last_event_id",
            name=op.f("uq_kill_switch_states_last_event_id"),
        ),
        schema="quantora",
    )
    op.execute(
        """
        CREATE FUNCTION quantora.reject_kill_switch_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'kill-switch audit events are append-only'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_kill_switch_events_append_only
        BEFORE UPDATE OR DELETE ON quantora.kill_switch_events
        FOR EACH ROW
        EXECUTE FUNCTION quantora.reject_kill_switch_event_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION quantora.validate_kill_switch_state()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            event_row quantora.kill_switch_events%ROWTYPE;
        BEGIN
            SELECT * INTO event_row
            FROM quantora.kill_switch_events
            WHERE id = NEW.last_event_id;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'kill-switch state references a missing event'
                    USING ERRCODE = '23503';
            END IF;

            IF NEW.scope_key IS DISTINCT FROM event_row.scope_key
                OR NEW.scope_kind IS DISTINCT FROM event_row.scope_kind
                OR NEW.scope_value IS DISTINCT FROM event_row.scope_value
                OR NEW.active IS DISTINCT FROM (event_row.action = 'activate')
                OR NEW.last_transition_at IS DISTINCT FROM event_row.occurred_at
            THEN
                RAISE EXCEPTION 'kill-switch state does not agree with its last event'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_kill_switch_states_validate_event
        BEFORE INSERT OR UPDATE ON quantora.kill_switch_states
        FOR EACH ROW
        EXECUTE FUNCTION quantora.validate_kill_switch_state()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_kill_switch_states_validate_event "
        "ON quantora.kill_switch_states"
    )
    op.execute("DROP FUNCTION IF EXISTS quantora.validate_kill_switch_state()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_kill_switch_events_append_only ON quantora.kill_switch_events"
    )
    op.execute("DROP FUNCTION IF EXISTS quantora.reject_kill_switch_event_mutation()")
    op.drop_table("kill_switch_states", schema="quantora")
    op.drop_table("kill_switch_events", schema="quantora")
