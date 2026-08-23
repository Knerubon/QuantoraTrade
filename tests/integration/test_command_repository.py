"""PostgreSQL integration tests for the durable PAPER command queue."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantora_trade.infrastructure.database.command_repository import (
    IdempotencyConflictError,
    LeaseOwnershipError,
    PostgresSystemCommandRepository,
)

DATABASE_URL = os.getenv("QUANTORA_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("QUANTORA_DATABASE_URL is required for integration tests", allow_module_level=True)

engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(engine, expire_on_commit=False)
NOW = datetime(2026, 8, 23, 4, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_commands() -> None:
    with SessionFactory() as session, session.begin():
        session.execute(text("TRUNCATE quantora.system_commands"))


def enqueue(repository: PostgresSystemCommandRepository, request_hash: str = "a" * 64):
    return repository.enqueue(
        request_id="request-1",
        idempotency_key="key-1",
        request_hash=request_hash,
        action="start",
        mode="paper",
        payload={
            "mode": "paper",
            "symbols": ["XAUUSD"],
            "strategy_id": "trend-v1",
            "reason": "operator requested",
        },
        actor="operator@example.test",
    )


def test_command_survives_restart_and_same_request_replays() -> None:
    command_id = UUID("00000000-0000-0000-0000-000000000006")
    repository = PostgresSystemCommandRepository(
        SessionFactory, now=lambda: NOW, new_id=lambda: command_id
    )
    first = enqueue(repository)
    replay = enqueue(repository)

    assert first.created is True
    assert replay.created is False
    assert replay.command == first.command
    assert PostgresSystemCommandRepository(SessionFactory).get(command_id) == first.command


def test_idempotency_key_cannot_be_rebound_by_same_actor() -> None:
    repository = PostgresSystemCommandRepository(SessionFactory, now=lambda: NOW)
    enqueue(repository)

    with pytest.raises(IdempotencyConflictError, match="another request"):
        enqueue(repository, "b" * 64)


def test_hash_must_be_lowercase_sha256_hex() -> None:
    repository = PostgresSystemCommandRepository(SessionFactory, now=lambda: NOW)

    with pytest.raises(ValueError, match="SHA-256"):
        enqueue(repository, "G" * 64)


def test_only_one_concurrent_worker_can_claim_a_command() -> None:
    repository = PostgresSystemCommandRepository(SessionFactory, now=lambda: NOW)
    enqueue(repository)

    def claim(worker: str):
        return PostgresSystemCommandRepository(SessionFactory, now=lambda: NOW).claim(
            worker_id=worker, lease_duration=timedelta(seconds=30)
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ["worker-a", "worker-b"]))

    owned = [command for command in claims if command is not None]
    assert len(owned) == 1
    assert owned[0].attempts == 1


def test_later_stop_cannot_overtake_earlier_live_start() -> None:
    repository = PostgresSystemCommandRepository(SessionFactory, now=lambda: NOW)
    start = enqueue(repository).command
    repository.enqueue(
        request_id="request-2",
        idempotency_key="key-2",
        request_hash="b" * 64,
        action="stop",
        mode="paper",
        payload={"mode": "paper", "reason": "operator stop"},
        actor="operator@example.test",
    )
    claimed = repository.claim(worker_id="worker-a", lease_duration=timedelta(seconds=30))
    assert claimed is not None and claimed.id == start.id
    assert repository.claim(worker_id="worker-b", lease_duration=timedelta(seconds=30)) is None


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self._lock = Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self.value

    def advance(self, delta: timedelta) -> None:
        with self._lock:
            self.value += delta


def test_expired_lease_is_recovered_and_old_worker_is_fenced() -> None:
    clock = MutableClock(NOW)
    repository = PostgresSystemCommandRepository(SessionFactory, now=clock)
    command_id = enqueue(repository).command.id
    first = repository.claim(worker_id="worker-a", lease_duration=timedelta(seconds=10))
    assert first is not None and first.lease_token is not None

    clock.advance(timedelta(seconds=11))
    recovered = repository.claim(worker_id="worker-b", lease_duration=timedelta(seconds=10))
    assert recovered is not None and recovered.id == command_id
    assert recovered.attempts == 2
    assert recovered.lease_token != first.lease_token

    with pytest.raises(LeaseOwnershipError, match="fenced"):
        repository.acknowledge(
            command_id,
            worker_id="worker-a",
            lease_token=first.lease_token,
            result={"state": "running"},
        )

    completed = repository.acknowledge(
        command_id,
        worker_id="worker-b",
        lease_token=recovered.lease_token,
        result={"state": "running"},
    )
    assert completed.status.value == "succeeded"
    assert completed.completed_at == clock.value
    with pytest.raises(LeaseOwnershipError):
        repository.fail(
            command_id,
            worker_id="worker-b",
            lease_token=recovered.lease_token,
            result={"error": "late failure"},
        )


def test_heartbeat_extends_lease_and_audit_payload_is_immutable() -> None:
    clock = MutableClock(NOW)
    repository = PostgresSystemCommandRepository(SessionFactory, now=clock)
    command_id = enqueue(repository).command.id
    claimed = repository.claim(worker_id="worker-a", lease_duration=timedelta(seconds=10))
    assert claimed is not None and claimed.lease_token is not None
    clock.advance(timedelta(seconds=5))
    heartbeat = repository.heartbeat(
        command_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token,
        lease_duration=timedelta(seconds=20),
    )
    assert heartbeat.lease_expires_at == clock.value + timedelta(seconds=20)

    with (
        pytest.raises(Exception, match="immutable"),
        SessionFactory() as session,
        session.begin(),
    ):
        session.execute(
            text("UPDATE quantora.system_commands SET payload = '{}'::jsonb WHERE id = :id"),
            {"id": command_id},
        )
