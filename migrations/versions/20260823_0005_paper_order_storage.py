"""Add durable PAPER order, event, and fill storage.

Revision ID: 20260823_0005
Revises: 20260823_0004
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823_0005"
down_revision: str | None = "20260823_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("approved_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("specification_hash", sa.String(64), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("contract_multiplier", sa.Numeric(30, 12), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("requested_volume", sa.Numeric(30, 12), nullable=False),
        sa.Column("point", sa.Numeric(30, 12), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("filled_volume", sa.Numeric(30, 12), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("mode = 'paper'", name=op.f("ck_paper_orders_paper_mode_only")),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_paper_orders_request_hash_valid"),
        ),
        sa.CheckConstraint(
            "specification_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_paper_orders_specification_hash_valid"),
        ),
        sa.CheckConstraint(
            "quote_currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_paper_orders_quote_currency_valid"),
        ),
        sa.CheckConstraint(
            "contract_multiplier > 0",
            name=op.f("ck_paper_orders_contract_multiplier_positive"),
        ),
        sa.CheckConstraint(
            "requested_volume > 0 AND point > 0",
            name=op.f("ck_paper_orders_positive_request_values"),
        ),
        sa.CheckConstraint(
            "filled_volume >= 0 AND filled_volume <= requested_volume",
            name=op.f("ck_paper_orders_filled_volume_valid"),
        ),
        sa.CheckConstraint("side IN ('buy', 'sell')", name=op.f("ck_paper_orders_side_valid")),
        sa.CheckConstraint(
            "status IN ('created','accepted','partial','filled','cancel_pending',"
            "'cancelled','rejected','expired','unknown')",
            name=op.f("ck_paper_orders_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_orders")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_paper_orders_idempotency_key")),
        sa.UniqueConstraint("request_hash", name="uq_paper_orders_request_hash"),
        schema="quantora",
    )
    op.create_table(
        "paper_order_events",
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_paper_order_events_positive_sequence")),
        sa.CheckConstraint(
            "status IN ('created','accepted','partial','filled','cancel_pending',"
            "'cancelled','rejected','expired','unknown')",
            name=op.f("ck_paper_order_events_status_valid"),
        ),
        sa.ForeignKeyConstraint(["order_id"], ["quantora.paper_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("order_id", "sequence", name=op.f("pk_paper_order_events")),
        sa.UniqueConstraint("order_id", "sequence", name="uq_paper_order_events_sequence"),
        schema="quantora",
    )
    op.create_table(
        "paper_fills",
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("volume", sa.Numeric(30, 12), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("commission", sa.Numeric(30, 12), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_paper_fills_positive_sequence")),
        sa.CheckConstraint(
            "volume > 0 AND price > 0 AND commission >= 0",
            name=op.f("ck_paper_fills_valid_values"),
        ),
        sa.ForeignKeyConstraint(["order_id"], ["quantora.paper_orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("order_id", "sequence", name=op.f("pk_paper_fills")),
        sa.UniqueConstraint("order_id", "sequence", name="uq_paper_fills_sequence"),
        schema="quantora",
    )
    op.execute(
        """
        CREATE FUNCTION quantora.reject_paper_evidence_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'paper execution evidence is append-only' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table in ("paper_order_events", "paper_fills"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON quantora.{table}
            FOR EACH ROW EXECUTE FUNCTION quantora.reject_paper_evidence_mutation()
            """
        )
    op.execute(
        """
        CREATE FUNCTION quantora.validate_paper_order_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.id IS DISTINCT FROM NEW.id
                OR OLD.idempotency_key IS DISTINCT FROM NEW.idempotency_key
                OR OLD.request_hash IS DISTINCT FROM NEW.request_hash
                OR OLD.approved_intent_id IS DISTINCT FROM NEW.approved_intent_id
                OR OLD.instrument_id IS DISTINCT FROM NEW.instrument_id
                OR OLD.broker_id IS DISTINCT FROM NEW.broker_id
                OR OLD.specification_hash IS DISTINCT FROM NEW.specification_hash
                OR OLD.quote_currency IS DISTINCT FROM NEW.quote_currency
                OR OLD.contract_multiplier IS DISTINCT FROM NEW.contract_multiplier
                OR OLD.mode IS DISTINCT FROM NEW.mode
                OR OLD.symbol IS DISTINCT FROM NEW.symbol
                OR OLD.side IS DISTINCT FROM NEW.side
                OR OLD.requested_volume IS DISTINCT FROM NEW.requested_volume
                OR OLD.point IS DISTINCT FROM NEW.point
                OR OLD.expires_at IS DISTINCT FROM NEW.expires_at THEN
                RAISE EXCEPTION 'paper order request identity is immutable' USING ERRCODE = '55000';
            END IF;
            IF NEW.version <= OLD.version OR NEW.filled_volume < OLD.filled_volume THEN
                RAISE EXCEPTION 'paper order update is stale' USING ERRCODE = '40001';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_order_update
        BEFORE UPDATE ON quantora.paper_orders
        FOR EACH ROW EXECUTE FUNCTION quantora.validate_paper_order_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION quantora.validate_paper_order_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_status text := OLD.status;
        DECLARE item record;
        BEGIN
            IF current_status IN ('filled', 'cancelled', 'rejected', 'expired', 'unknown') THEN
                RAISE EXCEPTION 'terminal paper order cannot transition' USING ERRCODE = '55000';
            END IF;
            FOR item IN
                SELECT sequence, status FROM quantora.paper_order_events
                WHERE order_id = NEW.id AND sequence > OLD.version
                ORDER BY sequence
            LOOP
                IF NOT (
                    (current_status = 'created' AND item.status IN
                        ('accepted', 'rejected', 'expired'))
                    OR (current_status = 'accepted' AND item.status IN
                        ('partial', 'filled', 'cancel_pending', 'expired'))
                    OR (current_status = 'partial' AND item.status IN
                        ('partial', 'filled', 'cancel_pending', 'expired'))
                    OR (current_status = 'cancel_pending' AND item.status = 'cancelled')
                ) THEN
                    RAISE EXCEPTION 'invalid paper order state transition' USING ERRCODE = '55000';
                END IF;
                current_status := item.status;
            END LOOP;
            IF current_status <> NEW.status THEN
                RAISE EXCEPTION 'paper order transition evidence is incomplete'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_paper_order_transition
        AFTER UPDATE ON quantora.paper_orders
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION quantora.validate_paper_order_transition()
        """
    )
    op.execute(
        """
        CREATE FUNCTION quantora.validate_paper_order_totals()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target_id uuid;
        DECLARE requested numeric;
        DECLARE recorded numeric;
        DECLARE snapshot numeric;
        DECLARE latest_sequence integer;
        DECLARE latest_status text;
        DECLARE event_count integer;
        DECLARE current_status text;
        DECLARE item record;
        DECLARE snapshot_status text;
        DECLARE snapshot_version integer;
        BEGIN
            IF TG_TABLE_NAME = 'paper_orders' THEN
                target_id := NEW.id;
            ELSE
                target_id := NEW.order_id;
            END IF;
            SELECT requested_volume, filled_volume, status, version
              INTO requested, snapshot, snapshot_status, snapshot_version
              FROM quantora.paper_orders WHERE id = target_id;
            SELECT COALESCE(SUM(volume), 0) INTO recorded
              FROM quantora.paper_fills WHERE order_id = target_id;
            SELECT sequence, status INTO latest_sequence, latest_status
              FROM quantora.paper_order_events WHERE order_id = target_id
              ORDER BY sequence DESC LIMIT 1;
            SELECT COUNT(*) INTO event_count
              FROM quantora.paper_order_events WHERE order_id = target_id;
            IF recorded <> snapshot OR recorded > requested THEN
                RAISE EXCEPTION 'paper fill total does not match order snapshot'
                    USING ERRCODE = '23514';
            END IF;
            IF latest_sequence IS NULL OR latest_sequence <> snapshot_version
                OR event_count <> snapshot_version
                OR latest_status <> snapshot_status THEN
                RAISE EXCEPTION 'paper event sequence does not match order snapshot'
                    USING ERRCODE = '23514';
            END IF;
            current_status := NULL;
            FOR item IN
                SELECT sequence, status FROM quantora.paper_order_events
                WHERE order_id = target_id ORDER BY sequence
            LOOP
                IF item.sequence = 1 THEN
                    IF item.status <> 'created' THEN
                        RAISE EXCEPTION 'paper order must begin in created state'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NOT (
                    (current_status = 'created' AND item.status IN
                        ('accepted', 'rejected', 'expired'))
                    OR (current_status = 'accepted' AND item.status IN
                        ('partial', 'filled', 'cancel_pending', 'expired'))
                    OR (current_status = 'partial' AND item.status IN
                        ('partial', 'filled', 'cancel_pending', 'expired'))
                    OR (current_status = 'cancel_pending' AND item.status = 'cancelled')
                ) THEN
                    RAISE EXCEPTION 'invalid paper order event transition'
                        USING ERRCODE = '23514';
                END IF;
                current_status := item.status;
            END LOOP;
            RETURN NEW;
        END;
        $$
        """
    )
    for table in ("paper_orders", "paper_order_events", "paper_fills"):
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER trg_{table}_validate_snapshot
            AFTER INSERT OR UPDATE ON quantora.{table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION quantora.validate_paper_order_totals()
            """
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_paper_order_transition ON quantora.paper_orders")
    op.execute("DROP FUNCTION IF EXISTS quantora.validate_paper_order_transition()")
    for table in ("paper_fills", "paper_order_events", "paper_orders"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_validate_snapshot ON quantora.{table}")
    op.execute("DROP FUNCTION IF EXISTS quantora.validate_paper_order_totals()")
    op.execute("DROP TRIGGER IF EXISTS trg_paper_order_update ON quantora.paper_orders")
    op.execute("DROP FUNCTION IF EXISTS quantora.validate_paper_order_update()")
    for table in ("paper_fills", "paper_order_events"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON quantora.{table}")
    op.execute("DROP FUNCTION IF EXISTS quantora.reject_paper_evidence_mutation()")
    op.drop_table("paper_fills", schema="quantora")
    op.drop_table("paper_order_events", schema="quantora")
    op.drop_table("paper_orders", schema="quantora")
