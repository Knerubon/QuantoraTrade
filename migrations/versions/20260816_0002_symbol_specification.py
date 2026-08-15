"""Complete broker symbol specification.

Revision ID: 20260816_0002
Revises: 20260816_0001
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0002"
down_revision: str | None = "20260816_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instruments",
        sa.Column("pip_size", sa.Numeric(24, 12), nullable=False, server_default="1"),
        schema="quantora",
    )
    op.add_column(
        "instruments",
        sa.Column("spread_points", sa.Integer(), nullable=False, server_default="0"),
        schema="quantora",
    )
    op.add_column(
        "instruments",
        sa.Column("session_timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        schema="quantora",
    )
    op.add_column(
        "instruments",
        sa.Column(
            "session_profile",
            sa.String(length=64),
            nullable=False,
            server_default="broker_defined",
        ),
        schema="quantora",
    )
    op.create_check_constraint(
        op.f("ck_instruments_pip_size_positive"),
        "instruments",
        "pip_size > 0",
        schema="quantora",
    )
    for column_name in (
        "pip_size",
        "spread_points",
        "session_timezone",
        "session_profile",
    ):
        op.alter_column(
            "instruments",
            column_name,
            server_default=None,
            schema="quantora",
        )
    op.create_check_constraint(
        op.f("ck_instruments_spread_points_non_negative"),
        "instruments",
        "spread_points >= 0",
        schema="quantora",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_instruments_spread_points_non_negative"),
        "instruments",
        type_="check",
        schema="quantora",
    )
    op.drop_constraint(
        op.f("ck_instruments_pip_size_positive"),
        "instruments",
        type_="check",
        schema="quantora",
    )
    op.drop_column("instruments", "session_profile", schema="quantora")
    op.drop_column("instruments", "session_timezone", schema="quantora")
    op.drop_column("instruments", "spread_points", schema="quantora")
    op.drop_column("instruments", "pip_size", schema="quantora")
