"""SQLAlchemy models for durable kill-switch state and its audit log."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from quantora_trade.infrastructure.database.models import Base


class KillSwitchEventModel(Base):
    """Append-only record of a requested kill-switch transition."""

    __tablename__ = "kill_switch_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('activate', 'deactivate')",
            name="action_valid",
        ),
        CheckConstraint(
            "scope_kind IN ('global', 'account', 'asset', 'symbol', 'strategy', 'new_entries')",
            name="scope_kind_valid",
        ),
        CheckConstraint(
            "(scope_kind IN ('global', 'new_entries') AND scope_value IS NULL) "
            "OR (scope_kind NOT IN ('global', 'new_entries') AND scope_value IS NOT NULL)",
            name="scope_value_valid",
        ),
        CheckConstraint(
            "scope_key = scope_kind || ':' || COALESCE(scope_value, '*')",
            name="scope_key_consistent",
        ),
        {"schema": "quantora"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(300), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_value: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    incident_reference: Mapped[str] = mapped_column(String(255), nullable=False)


class KillSwitchStateModel(Base):
    """Current materialized state; the event table remains the source of audit history."""

    __tablename__ = "kill_switch_states"
    __table_args__ = (
        CheckConstraint(
            "scope_kind IN ('global', 'account', 'asset', 'symbol', 'strategy', 'new_entries')",
            name="scope_kind_valid",
        ),
        CheckConstraint(
            "(scope_kind IN ('global', 'new_entries') AND scope_value IS NULL) "
            "OR (scope_kind NOT IN ('global', 'new_entries') AND scope_value IS NOT NULL)",
            name="scope_value_valid",
        ),
        CheckConstraint(
            "scope_key = scope_kind || ':' || COALESCE(scope_value, '*')",
            name="scope_key_consistent",
        ),
        {"schema": "quantora"},
    )

    scope_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_value: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quantora.kill_switch_events.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    last_transition_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["KillSwitchEventModel", "KillSwitchStateModel"]
