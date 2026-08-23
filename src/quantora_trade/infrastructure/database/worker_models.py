"""Durable PAPER worker state and transition audit models."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from quantora_trade.infrastructure.database.models import Base


class PaperWorkerStateModel(Base):
    __tablename__ = "paper_worker_states"
    __table_args__ = (
        CheckConstraint("id = 'paper'", name="singleton_id"),
        CheckConstraint(
            "status IN ('stopped','starting','running','stopping','degraded','halted')",
            name="status_valid",
        ),
        CheckConstraint("version >= 0", name="version_nonnegative"),
        CheckConstraint("(config IS NULL) = (config_hash IS NULL)", name="config_hash_consistent"),
        CheckConstraint(
            "last_heartbeat_at IS NULL OR last_heartbeat_at >= changed_at",
            name="heartbeat_monotonic",
        ),
        CheckConstraint(
            "(active_generation IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND lease_heartbeat_at IS NULL) OR "
            "(active_generation IS NOT NULL AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND lease_heartbeat_at IS NOT NULL)",
            name="lease_consistent",
        ),
        CheckConstraint(
            "lease_owner IS NULL OR length(lease_owner) > 0", name="lease_owner_nonempty"
        ),
        CheckConstraint(
            "lease_expires_at IS NULL OR lease_expires_at > lease_heartbeat_at",
            name="lease_window_positive",
        ),
        {"schema": "quantora"},
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    config_hash: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(500))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_generation: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PaperWorkerTransitionModel(Base):
    __tablename__ = "paper_worker_transitions"
    __table_args__ = (
        CheckConstraint("length(command_id) > 0", name="command_id_nonempty"),
        CheckConstraint("fingerprint ~ '^[[:print:]]+$'", name="fingerprint_nonempty"),
        {"schema": "quantora"},
    )

    command_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(500), nullable=False)
    result: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["PaperWorkerStateModel", "PaperWorkerTransitionModel"]
