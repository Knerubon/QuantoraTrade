"""PostgreSQL adapter for the durable PAPER worker lifecycle."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quantora_trade.application.paper_worker import (
    PaperWorkerConfig,
    PaperWorkerState,
    StoredWorkerCommand,
    WorkerStatus,
)
from quantora_trade.infrastructure.database.worker_models import (
    PaperWorkerStateModel,
    PaperWorkerTransitionModel,
)


class WorkerStateConflictError(RuntimeError):
    """The worker state changed since it was read."""


class WorkloadLeaseFencedError(PermissionError):
    """The durable PAPER workload lease is absent, expired, or owned elsewhere."""


class PostgresPaperWorkerRepository:
    """Store each lifecycle transition and singleton state in one transaction."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._now = now

    def current(self) -> PaperWorkerState:
        with self._session_factory() as session, session.begin():
            row = session.get(PaperWorkerStateModel, "paper")
            if row is None:
                now = _aware(self._now())
                session.execute(
                    insert(PaperWorkerStateModel)
                    .values(id="paper", status="stopped", changed_at=now, version=0)
                    .on_conflict_do_nothing(index_elements=["id"])
                )
                row = session.get(PaperWorkerStateModel, "paper")
            if row is None:
                raise RuntimeError("paper worker state initialization failed")
            return _state(row)

    def command(self, command_id: str) -> StoredWorkerCommand | None:
        with self._session_factory() as session:
            row = session.get(PaperWorkerTransitionModel, command_id)
            return None if row is None else _stored(row)

    def persist(
        self, *, expected: PaperWorkerState, command: StoredWorkerCommand
    ) -> PaperWorkerState:
        with self._session_factory() as session, session.begin():
            replay = session.get(PaperWorkerTransitionModel, command.command_id)
            if replay is not None:
                stored = _stored(replay)
                if stored.fingerprint != command.fingerprint:
                    raise ValueError("command_id reused for a different command")
                return stored.result
            row = session.scalar(
                select(PaperWorkerStateModel)
                .where(PaperWorkerStateModel.id == "paper")
                .with_for_update()
            )
            if row is None or _state(row) != expected:
                raise WorkerStateConflictError("optimistic worker state conflict")
            result = command.result
            row.status = result.status.value
            row.config = None if result.config is None else _config_json(result.config)
            row.config_hash = result.config_hash
            row.reason = result.reason
            row.changed_at = result.changed_at
            row.last_heartbeat_at = None
            row.version += 1
            session.add(
                PaperWorkerTransitionModel(
                    command_id=command.command_id,
                    fingerprint=command.fingerprint,
                    result=_state_json(result),
                    created_at=_aware(self._now()),
                )
            )
            session.flush()
            return _state(row)

    def heartbeat(self) -> PaperWorkerState:
        """Persist liveness without changing lifecycle state or enabling entries."""

        now = _aware(self._now())
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                update(PaperWorkerStateModel)
                .where(PaperWorkerStateModel.id == "paper")
                .values(last_heartbeat_at=now)
                .returning(PaperWorkerStateModel)
            )
            if row is None:
                raise RuntimeError("paper worker state is not initialized")
            return _state(row)

    def acquire_workload_lease(
        self, *, owner: str, generation: UUID, lease_duration: timedelta
    ) -> None:
        now, expires = self._lease_window(owner, lease_duration)
        with self._session_factory() as session, session.begin():
            acquired = session.scalar(
                update(PaperWorkerStateModel)
                .where(
                    PaperWorkerStateModel.id == "paper",
                    or_(
                        PaperWorkerStateModel.active_generation.is_(None),
                        PaperWorkerStateModel.lease_expires_at <= now,
                        and_(
                            PaperWorkerStateModel.lease_owner == owner,
                            PaperWorkerStateModel.active_generation == generation,
                        ),
                    ),
                )
                .values(
                    active_generation=generation,
                    lease_owner=owner,
                    lease_heartbeat_at=now,
                    lease_expires_at=expires,
                )
                .returning(PaperWorkerStateModel.active_generation)
            )
            if acquired is None:
                raise WorkloadLeaseFencedError("PAPER workload lease is owned by another host")

    def renew_workload_lease(
        self, *, owner: str, generation: UUID, lease_duration: timedelta
    ) -> None:
        now, expires = self._lease_window(owner, lease_duration)
        with self._session_factory() as session, session.begin():
            renewed = session.scalar(
                update(PaperWorkerStateModel)
                .where(
                    PaperWorkerStateModel.id == "paper",
                    PaperWorkerStateModel.lease_owner == owner,
                    PaperWorkerStateModel.active_generation == generation,
                    PaperWorkerStateModel.lease_expires_at > now,
                )
                .values(lease_heartbeat_at=now, lease_expires_at=expires)
                .returning(PaperWorkerStateModel.active_generation)
            )
            if renewed is None:
                raise WorkloadLeaseFencedError("PAPER workload generation is fenced")

    def release_workload_lease(self, *, owner: str, generation: UUID) -> None:
        now = _aware(self._now())
        with self._session_factory() as session, session.begin():
            released = session.scalar(
                update(PaperWorkerStateModel)
                .where(
                    PaperWorkerStateModel.id == "paper",
                    PaperWorkerStateModel.lease_owner == owner,
                    PaperWorkerStateModel.active_generation == generation,
                    PaperWorkerStateModel.lease_expires_at > now,
                )
                .values(
                    active_generation=None,
                    lease_owner=None,
                    lease_heartbeat_at=None,
                    lease_expires_at=None,
                )
                .returning(PaperWorkerStateModel.id)
            )
            if released is None:
                raise WorkloadLeaseFencedError("PAPER workload release is fenced")

    def _lease_window(self, owner: str, duration: timedelta) -> tuple[datetime, datetime]:
        if not owner.strip() or owner != owner.strip():
            raise ValueError("lease owner must be non-empty and trimmed")
        if duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        now = _aware(self._now())
        return now, now + duration


def _config_json(value: PaperWorkerConfig) -> dict[str, object]:
    return {
        "account": value.account,
        "strategy_id": value.strategy_id,
        "symbols": list(value.symbols),
        "timeframes": list(value.timeframes),
        "polling_interval_seconds": str(value.polling_interval_seconds),
    }


def _config(value: object) -> PaperWorkerConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeError("invalid persisted worker config")
    return PaperWorkerConfig(
        account=str(value["account"]),
        strategy_id=str(value["strategy_id"]),
        symbols=tuple(str(item) for item in value["symbols"]),
        timeframes=tuple(str(item) for item in value["timeframes"]),
        polling_interval_seconds=Decimal(str(value["polling_interval_seconds"])),
    )


def _state_json(value: PaperWorkerState) -> dict[str, object]:
    return {
        "status": value.status.value,
        "changed_at": value.changed_at.isoformat(),
        "config": None if value.config is None else _config_json(value.config),
        "config_hash": value.config_hash,
        "reason": value.reason,
        "revision": value.revision,
    }


def _state(row: PaperWorkerStateModel) -> PaperWorkerState:
    config = _config(row.config)
    return PaperWorkerState(
        status=WorkerStatus(row.status),
        changed_at=_utc(row.changed_at),
        config=config,
        config_hash=row.config_hash,
        reason=row.reason,
        revision=row.version,
    )


def _stored(row: PaperWorkerTransitionModel) -> StoredWorkerCommand:
    payload = row.result
    config = _config(payload.get("config"))
    state = PaperWorkerState(
        status=WorkerStatus(str(payload["status"])),
        changed_at=datetime.fromisoformat(str(payload["changed_at"])).astimezone(UTC),
        config=config,
        config_hash=None if payload.get("config_hash") is None else str(payload["config_hash"]),
        reason=None if payload.get("reason") is None else str(payload["reason"]),
        revision=int(str(payload.get("revision", 0))),
    )
    return StoredWorkerCommand(row.command_id, row.fingerprint, state)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("repository clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


__all__ = [
    "PostgresPaperWorkerRepository",
    "WorkerStateConflictError",
    "WorkloadLeaseFencedError",
]
