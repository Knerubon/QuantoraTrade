"""Durable command-queue models for the PAPER control plane."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from quantora_trade.infrastructure.database.models import Base


class SystemCommandModel(Base):
    """A command accepted by the API and consumed asynchronously by a worker."""

    __tablename__ = "system_commands"
    __table_args__ = (
        CheckConstraint("action IN ('start', 'stop')", name="action_valid"),
        CheckConstraint("mode = 'paper'", name="paper_mode_only"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed')",
            name="status_valid",
        ),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash_valid"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("updated_at >= created_at", name="timestamps_monotonic"),
        CheckConstraint(
            "(status = 'queued' AND worker_id IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL AND completed_at IS NULL AND result IS NULL) OR "
            "(status = 'processing' AND worker_id IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND completed_at IS NULL AND result IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND worker_id IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND result IS NOT NULL)",
            name="state_consistent",
        ),
        CheckConstraint(
            "last_heartbeat_at IS NULL OR last_heartbeat_at >= created_at",
            name="heartbeat_monotonic",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="completion_monotonic",
        ),
        UniqueConstraint(
            "actor",
            "idempotency_key",
            name="uq_system_commands_actor_idempotency_key_unique",
        ),
        {"schema": "quantora"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    queue_sequence: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), nullable=False, unique=True
    )
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    worker_id: Mapped[str | None] = mapped_column(String(255))
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["SystemCommandModel"]
