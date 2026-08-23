"""Add immutable PAPER accounting evidence and current projections.

Revision ID: 20260823_0008
Revises: 20260823_0007
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823_0008"
down_revision: str | None = "20260823_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_accounts",
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("initial_balance", sa.Numeric(30, 12), nullable=False),
        sa.Column("cash_balance", sa.Numeric(30, 12), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(30, 12), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(30, 12), nullable=False),
        sa.Column("fees", sa.Numeric(30, 12), nullable=False),
        sa.Column("equity", sa.Numeric(30, 12), nullable=False),
        sa.Column("equity_peak", sa.Numeric(30, 12), nullable=False),
        sa.Column("drawdown", sa.Numeric(30, 12), nullable=False),
        sa.Column("drawdown_pct", sa.Numeric(30, 18), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_paper_accounts_currency_valid"),
        ),
        sa.CheckConstraint(
            "fees >= 0 AND drawdown >= 0 AND drawdown_pct >= 0",
            name=op.f("ck_paper_accounts_totals_valid"),
        ),
        sa.CheckConstraint(
            "equity_peak >= equity AND drawdown = equity_peak - equity",
            name=op.f("ck_paper_accounts_drawdown_valid"),
        ),
        sa.PrimaryKeyConstraint("currency", name=op.f("pk_paper_accounts")),
        schema="quantora",
    )
    op.create_table(
        "paper_positions",
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("specification_hash", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("net_quantity", sa.Numeric(30, 12), nullable=False),
        sa.Column("average_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("mark_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("contract_multiplier", sa.Numeric(30, 12), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(30, 12), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(30, 12), nullable=False),
        sa.Column("fees", sa.Numeric(30, 12), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quote_currency ~ '^[A-Z]{3}$'", name=op.f("ck_paper_positions_currency_valid")
        ),
        sa.CheckConstraint(
            "average_price >= 0 AND mark_price >= 0",
            name=op.f("ck_paper_positions_prices_valid"),
        ),
        sa.CheckConstraint(
            "contract_multiplier > 0 AND fees >= 0",
            name=op.f("ck_paper_positions_values_valid"),
        ),
        sa.CheckConstraint(
            "net_quantity <> 0 OR (average_price = 0 AND unrealized_pnl = 0)",
            name=op.f("ck_paper_positions_flat_position_valid"),
        ),
        sa.PrimaryKeyConstraint("instrument_id", name=op.f("pk_paper_positions")),
        schema="quantora",
    )
    op.create_table(
        "paper_accounting_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fill_sequence", sa.Integer(), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("specification_hash", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Numeric(30, 12), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("commission", sa.Numeric(30, 12), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("contract_multiplier", sa.Numeric(30, 12), nullable=False),
        sa.Column("realized_delta", sa.Numeric(30, 12), nullable=False),
        sa.Column("post_net_quantity", sa.Numeric(30, 12), nullable=False),
        sa.Column("post_average_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("post_cash_balance", sa.Numeric(30, 12), nullable=False),
        sa.Column("post_equity", sa.Numeric(30, 12), nullable=False),
        sa.Column("post_equity_peak", sa.Numeric(30, 12), nullable=False),
        sa.Column("post_drawdown", sa.Numeric(30, 12), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "side IN ('buy', 'sell')", name=op.f("ck_paper_accounting_events_side_valid")
        ),
        sa.CheckConstraint(
            "quantity > 0 AND price > 0 AND commission >= 0",
            name=op.f("ck_paper_accounting_events_fill_valid"),
        ),
        sa.CheckConstraint(
            "contract_multiplier > 0",
            name=op.f("ck_paper_accounting_events_multiplier_valid"),
        ),
        sa.CheckConstraint(
            "post_drawdown >= 0", name=op.f("ck_paper_accounting_events_drawdown_valid")
        ),
        sa.ForeignKeyConstraint(
            ["order_id", "fill_sequence"],
            ["quantora.paper_fills.order_id", "quantora.paper_fills.sequence"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_paper_accounting_events")),
        sa.UniqueConstraint("order_id", "fill_sequence", name="uq_accounting_fill_evidence"),
        schema="quantora",
    )
    op.create_table(
        "paper_mark_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("specification_hash", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prior_mark_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("post_unrealized_pnl", sa.Numeric(30, 12), nullable=False),
        sa.Column("post_equity", sa.Numeric(30, 12), nullable=False),
        sa.Column("post_equity_peak", sa.Numeric(30, 12), nullable=False),
        sa.Column("post_drawdown", sa.Numeric(30, 12), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("price > 0", name=op.f("ck_paper_mark_events_price_valid")),
        sa.CheckConstraint("post_drawdown >= 0", name=op.f("ck_paper_mark_events_drawdown_valid")),
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["quantora.paper_positions.instrument_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_paper_mark_events")),
        sa.UniqueConstraint("instrument_id", "observed_at", name="uq_paper_mark_observation"),
        schema="quantora",
    )
    op.execute(
        """
        CREATE FUNCTION quantora.reject_paper_accounting_event_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'paper accounting evidence is append-only' USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_mark_events_append_only
        BEFORE UPDATE OR DELETE ON quantora.paper_mark_events
        FOR EACH ROW EXECUTE FUNCTION quantora.reject_paper_accounting_event_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_paper_accounting_events_append_only
        BEFORE UPDATE OR DELETE ON quantora.paper_accounting_events
        FOR EACH ROW EXECUTE FUNCTION quantora.reject_paper_accounting_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_paper_mark_events_append_only ON quantora.paper_mark_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_paper_accounting_events_append_only "
        "ON quantora.paper_accounting_events"
    )
    op.drop_table("paper_mark_events", schema="quantora")
    op.execute("DROP FUNCTION IF EXISTS quantora.reject_paper_accounting_event_mutation()")
    op.drop_table("paper_accounting_events", schema="quantora")
    op.drop_table("paper_positions", schema="quantora")
    op.drop_table("paper_accounts", schema="quantora")
