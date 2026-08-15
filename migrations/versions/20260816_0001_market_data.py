"""Create market-data storage tables.

Revision ID: 20260816_0001
Revises:
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS quantora")

    op.create_table(
        "brokers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("adapter_type", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brokers")),
        sa.UniqueConstraint("code", name=op.f("uq_brokers_code")),
        schema="quantora",
    )
    op.create_table(
        "instruments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("canonical_symbol", sa.String(length=40), nullable=False),
        sa.Column("asset_class", sa.String(length=30), nullable=False),
        sa.Column("quote_currency", sa.String(length=10), nullable=False),
        sa.Column("digits", sa.Integer(), nullable=False),
        sa.Column("point", sa.Numeric(24, 12), nullable=False),
        sa.Column("tick_size", sa.Numeric(24, 12), nullable=False),
        sa.Column("tick_value", sa.Numeric(24, 8), nullable=False),
        sa.Column("contract_size", sa.Numeric(24, 8), nullable=False),
        sa.Column("volume_min", sa.Numeric(18, 8), nullable=False),
        sa.Column("volume_max", sa.Numeric(18, 8), nullable=False),
        sa.Column("volume_step", sa.Numeric(18, 8), nullable=False),
        sa.Column("specification_hash", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("contract_size > 0", name=op.f("ck_instruments_contract_size_positive")),
        sa.CheckConstraint("digits >= 0", name=op.f("ck_instruments_digits_non_negative")),
        sa.CheckConstraint("point > 0", name=op.f("ck_instruments_point_positive")),
        sa.CheckConstraint("tick_size > 0", name=op.f("ck_instruments_tick_size_positive")),
        sa.CheckConstraint("tick_value > 0", name=op.f("ck_instruments_tick_value_positive")),
        sa.CheckConstraint(
            "volume_max >= volume_min", name=op.f("ck_instruments_volume_range_valid")
        ),
        sa.CheckConstraint("volume_min > 0", name=op.f("ck_instruments_volume_min_positive")),
        sa.CheckConstraint("volume_step > 0", name=op.f("ck_instruments_volume_step_positive")),
        sa.ForeignKeyConstraint(
            ["broker_id"],
            ["quantora.brokers.id"],
            name=op.f("fk_instruments_broker_id_brokers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint("broker_id", "symbol", name="uq_instruments_broker_symbol"),
        schema="quantora",
    )
    op.create_table(
        "raw_market_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("open", sa.Numeric(24, 12), nullable=False),
        sa.Column("high", sa.Numeric(24, 12), nullable=False),
        sa.Column("low", sa.Numeric(24, 12), nullable=False),
        sa.Column("close", sa.Numeric(24, 12), nullable=False),
        sa.Column("tick_volume", sa.BigInteger(), nullable=False),
        sa.Column("spread_points", sa.Integer(), nullable=False),
        sa.Column("real_volume", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("high >= low", name=op.f("ck_raw_market_rates_raw_high_not_below_low")),
        sa.CheckConstraint(
            "tick_volume >= 0",
            name=op.f("ck_raw_market_rates_raw_tick_volume_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["quantora.instruments.id"],
            name=op.f("fk_raw_market_rates_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_market_rates")),
        sa.UniqueConstraint(
            "instrument_id",
            "timeframe",
            "open_time",
            "source",
            "payload_hash",
            name="uq_raw_rate_lineage",
        ),
        schema="quantora",
    )
    op.create_index(
        "ix_raw_rates_lookup",
        "raw_market_rates",
        ["instrument_id", "timeframe", "open_time"],
        schema="quantora",
    )
    op.create_table(
        "candles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(24, 12), nullable=False),
        sa.Column("high", sa.Numeric(24, 12), nullable=False),
        sa.Column("low", sa.Numeric(24, 12), nullable=False),
        sa.Column("close", sa.Numeric(24, 12), nullable=False),
        sa.Column("tick_volume", sa.BigInteger(), nullable=True),
        sa.Column("spread_points", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "close_time > open_time", name=op.f("ck_candles_candle_time_range_valid")
        ),
        sa.CheckConstraint("high >= low", name=op.f("ck_candles_candle_high_not_below_low")),
        sa.CheckConstraint(
            "tick_volume IS NULL OR tick_volume >= 0",
            name=op.f("ck_candles_tick_volume_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["quantora.instruments.id"],
            name=op.f("fk_candles_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candles")),
        sa.UniqueConstraint(
            "instrument_id",
            "timeframe",
            "open_time",
            "source",
            name="uq_candle_source_time",
        ),
        schema="quantora",
    )
    op.create_index(
        "ix_candles_lookup",
        "candles",
        ["instrument_id", "timeframe", "open_time"],
        schema="quantora",
    )
    op.create_table(
        "market_data_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=False),
        sa.Column("issue_code", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("candle_open_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["quantora.instruments.id"],
            name=op.f("fk_market_data_issues_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_data_issues")),
        schema="quantora",
    )
    op.create_index(
        "ix_market_data_issues_lookup",
        "market_data_issues",
        ["instrument_id", "timeframe", "detected_at"],
        schema="quantora",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_market_data_issues_lookup",
        table_name="market_data_issues",
        schema="quantora",
    )
    op.drop_table("market_data_issues", schema="quantora")
    op.drop_index("ix_candles_lookup", table_name="candles", schema="quantora")
    op.drop_table("candles", schema="quantora")
    op.drop_index("ix_raw_rates_lookup", table_name="raw_market_rates", schema="quantora")
    op.drop_table("raw_market_rates", schema="quantora")
    op.drop_table("instruments", schema="quantora")
    op.drop_table("brokers", schema="quantora")
    op.execute("DROP SCHEMA IF EXISTS quantora")
