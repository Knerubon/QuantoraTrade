"""Atomic PostgreSQL queue for PAPER control commands."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quantora_trade.infrastructure.database.command_models import SystemCommandModel


class CommandStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DurableSystemCommand:
    id: UUID
    request_id: str
    idempotency_key: str
    request_hash: str
    action: str
    mode: str
    payload: Mapping[str, object]
    actor: str
    status: CommandStatus
    created_at: datetime
    updated_at: datetime
    result: Mapping[str, object] | None = None
    worker_id: str | None = None
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    attempts: int = 0
    completed_at: datetime | None = None
    queue_sequence: int = 0


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    command: DurableSystemCommand
    created: bool


class IdempotencyConflictError(ValueError):
    """An actor reused an idempotency key for a different canonical request."""


class LeaseOwnershipError(RuntimeError):
    """The caller no longer owns a live lease; fail closed against stale workers."""


class PostgresSystemCommandRepository:
    """Enqueue atomically; API callers never execute the command inline."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._session_factory = session_factory
        self._now = now
        self._new_id = new_id

    def enqueue(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        request_hash: str,
        action: str,
        mode: str,
        payload: Mapping[str, object],
        actor: str,
    ) -> EnqueueResult:
        _required(request_id, "request_id")
        _required(idempotency_key, "idempotency_key")
        _required(actor, "actor")
        if len(request_hash) != 64 or any(
            character not in "0123456789abcdef" for character in request_hash
        ):
            raise ValueError("request_hash must be a SHA-256 hex digest")
        now = _aware_utc(self._now())
        command_id = self._new_id()
        with self._session_factory() as session, session.begin():
            statement = (
                insert(SystemCommandModel)
                .values(
                    id=command_id,
                    request_id=request_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    action=action,
                    mode=mode,
                    payload=dict(payload),
                    actor=actor,
                    status=CommandStatus.QUEUED.value,
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["actor", "idempotency_key"])
                .returning(SystemCommandModel.id)
            )
            created = session.scalar(statement) is not None
            row = session.scalar(
                select(SystemCommandModel).where(
                    SystemCommandModel.actor == actor,
                    SystemCommandModel.idempotency_key == idempotency_key,
                )
            )
            if row is None:
                raise RuntimeError("enqueued system command disappeared")
            if row.request_hash != request_hash:
                raise IdempotencyConflictError(
                    "idempotency key is already bound to another request"
                )
            return EnqueueResult(_command(row), created)

    def claim(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
    ) -> DurableSystemCommand | None:
        """Claim only the oldest active singleton command under a DB advisory lock."""

        _required(worker_id, "worker_id")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        now = _aware_utc(self._now())
        lease_token = self._new_id()
        with self._session_factory() as session, session.begin():
            # One PAPER runtime exists per database. Serializing claim selection prevents
            # a later STOP from overtaking an earlier START locked by another consumer.
            session.execute(text("SELECT pg_advisory_xact_lock(71520260823)"))
            candidate = session.scalar(
                select(SystemCommandModel)
                .where(
                    SystemCommandModel.status.in_(
                        (CommandStatus.QUEUED.value, CommandStatus.PROCESSING.value)
                    )
                )
                .order_by(SystemCommandModel.queue_sequence)
                .with_for_update()
                .limit(1)
            )
            if candidate is None:
                return None
            if (
                candidate.status == CommandStatus.PROCESSING.value
                and candidate.lease_expires_at is not None
                and _utc(candidate.lease_expires_at) > now
            ):
                return None
            candidate.status = CommandStatus.PROCESSING.value
            candidate.worker_id = worker_id
            candidate.lease_token = lease_token
            candidate.lease_expires_at = now + lease_duration
            candidate.last_heartbeat_at = now
            candidate.attempts += 1
            candidate.updated_at = now
            session.flush()
            return _command(candidate)

    def heartbeat(
        self,
        command_id: UUID,
        *,
        worker_id: str,
        lease_token: UUID,
        lease_duration: timedelta,
    ) -> DurableSystemCommand:
        """Extend a live lease only when its owner and fencing token still match."""

        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        now = _aware_utc(self._now())
        return self._owned_update(
            command_id,
            worker_id=worker_id,
            lease_token=lease_token,
            now=now,
            values={
                "lease_expires_at": now + lease_duration,
                "last_heartbeat_at": now,
                "updated_at": now,
            },
        )

    def acknowledge(
        self,
        command_id: UUID,
        *,
        worker_id: str,
        lease_token: UUID,
        result: Mapping[str, object],
    ) -> DurableSystemCommand:
        return self._finish(
            command_id,
            worker_id=worker_id,
            lease_token=lease_token,
            status=CommandStatus.SUCCEEDED,
            result=result,
        )

    def fail(
        self,
        command_id: UUID,
        *,
        worker_id: str,
        lease_token: UUID,
        result: Mapping[str, object],
    ) -> DurableSystemCommand:
        return self._finish(
            command_id,
            worker_id=worker_id,
            lease_token=lease_token,
            status=CommandStatus.FAILED,
            result=result,
        )

    def _finish(
        self,
        command_id: UUID,
        *,
        worker_id: str,
        lease_token: UUID,
        status: CommandStatus,
        result: Mapping[str, object],
    ) -> DurableSystemCommand:
        now = _aware_utc(self._now())
        return self._owned_update(
            command_id,
            worker_id=worker_id,
            lease_token=lease_token,
            now=now,
            values={
                "status": status.value,
                "result": dict(result),
                "completed_at": now,
                "updated_at": now,
            },
        )

    def _owned_update(
        self,
        command_id: UUID,
        *,
        worker_id: str,
        lease_token: UUID,
        now: datetime,
        values: Mapping[str, object],
    ) -> DurableSystemCommand:
        _required(worker_id, "worker_id")
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                update(SystemCommandModel)
                .where(
                    SystemCommandModel.id == command_id,
                    SystemCommandModel.status == CommandStatus.PROCESSING.value,
                    SystemCommandModel.worker_id == worker_id,
                    SystemCommandModel.lease_token == lease_token,
                    SystemCommandModel.lease_expires_at > now,
                    SystemCommandModel.updated_at <= now,
                )
                .values(**values)
                .returning(SystemCommandModel)
            )
            if row is None:
                raise LeaseOwnershipError("command lease is absent, expired, or fenced")
            return _command(row)

    def get(self, command_id: UUID) -> DurableSystemCommand | None:
        with self._session_factory() as session:
            row = session.get(SystemCommandModel, command_id)
            return None if row is None else _command(row)


def _required(value: str, name: str) -> None:
    if not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty and trimmed")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("repository clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _command(row: SystemCommandModel) -> DurableSystemCommand:
    return DurableSystemCommand(
        id=row.id,
        request_id=row.request_id,
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        action=row.action,
        mode=row.mode,
        payload=row.payload,
        actor=row.actor,
        status=CommandStatus(row.status),
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        result=row.result,
        worker_id=row.worker_id,
        lease_token=row.lease_token,
        lease_expires_at=None if row.lease_expires_at is None else _utc(row.lease_expires_at),
        last_heartbeat_at=(None if row.last_heartbeat_at is None else _utc(row.last_heartbeat_at)),
        attempts=row.attempts,
        completed_at=None if row.completed_at is None else _utc(row.completed_at),
        queue_sequence=row.queue_sequence,
    )


__all__ = [
    "CommandStatus",
    "DurableSystemCommand",
    "EnqueueResult",
    "IdempotencyConflictError",
    "LeaseOwnershipError",
    "PostgresSystemCommandRepository",
]
