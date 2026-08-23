"""PostgreSQL integration checks for durable PAPER worker recovery."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantora_trade.application.paper_worker import (
    PaperWorkerConfig,
    PaperWorkerControl,
    StartPaperWorker,
    WorkerStatus,
)
from quantora_trade.domain.enums import TradingMode
from quantora_trade.infrastructure.database.worker_repository import (
    PostgresPaperWorkerRepository,
    WorkloadLeaseFencedError,
)

DATABASE_URL = os.getenv("QUANTORA_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("QUANTORA_DATABASE_URL is required for integration tests", allow_module_level=True)

engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(engine, expire_on_commit=False)
NOW = datetime(2026, 8, 23, 5, tzinfo=UTC)
GEN_A = UUID("00000000-0000-0000-0000-0000000000a1")
GEN_B = UUID("00000000-0000-0000-0000-0000000000b2")


@pytest.fixture(autouse=True)
def clean_worker() -> None:
    with SessionFactory() as session, session.begin():
        session.execute(
            text("TRUNCATE quantora.paper_worker_transitions, quantora.paper_worker_states")
        )


class Gate:
    def new_entries_blocked(self) -> bool:
        return False


class Clock:
    def now(self) -> datetime:
        return NOW


def test_state_and_idempotent_transition_survive_repository_restart() -> None:
    repository = PostgresPaperWorkerRepository(SessionFactory, now=lambda: NOW)
    control = PaperWorkerControl(repository=repository, clock=Clock(), entry_gate=Gate())
    config = PaperWorkerConfig(
        account="paper-primary",
        strategy_id="trend-v1",
        symbols=("XAUUSD",),
        timeframes=("M5",),
        polling_interval_seconds=Decimal("5"),
    )
    request = StartPaperWorker("start-1", TradingMode.PAPER, config)

    first = control.start(request)
    restarted = PostgresPaperWorkerRepository(SessionFactory, now=lambda: NOW)

    assert restarted.current() == first
    assert (
        PaperWorkerControl(repository=restarted, clock=Clock(), entry_gate=Gate()).start(request)
        == first
    )
    assert first.revision == 1


def test_transition_audit_is_append_only_and_heartbeat_is_recovered() -> None:
    repository = PostgresPaperWorkerRepository(SessionFactory, now=lambda: NOW)
    repository.current()
    heartbeat = repository.heartbeat()
    assert heartbeat.status is WorkerStatus.STOPPED

    with SessionFactory() as session:
        persisted = session.execute(
            text("SELECT last_heartbeat_at FROM quantora.paper_worker_states WHERE id = 'paper'")
        ).scalar_one()
    assert persisted == NOW

    config = PaperWorkerConfig(
        account="paper-primary",
        strategy_id="trend-v1",
        symbols=("XAUUSD",),
        timeframes=("M5",),
        polling_interval_seconds=Decimal("5"),
    )
    PaperWorkerControl(repository=repository, clock=Clock(), entry_gate=Gate()).start(
        StartPaperWorker("start-audit", TradingMode.PAPER, config)
    )
    with (
        pytest.raises(Exception, match="append-only"),
        SessionFactory() as session,
        session.begin(),
    ):
        session.execute(
            text(
                "UPDATE quantora.paper_worker_transitions SET fingerprint = 'tampered' "
                "WHERE command_id = 'start-audit'"
            )
        )


def test_durable_lease_fences_two_hosts_and_supports_restart() -> None:
    owner_a = PostgresPaperWorkerRepository(SessionFactory, now=lambda: NOW)
    owner_a.current()
    owner_a.acquire_workload_lease(
        owner="host-a", generation=GEN_A, lease_duration=timedelta(seconds=30)
    )

    restarted = PostgresPaperWorkerRepository(SessionFactory, now=lambda: NOW)
    restarted.acquire_workload_lease(
        owner="host-a", generation=GEN_A, lease_duration=timedelta(seconds=30)
    )
    contender = PostgresPaperWorkerRepository(SessionFactory, now=lambda: NOW)
    with pytest.raises(WorkloadLeaseFencedError, match="another host"):
        contender.acquire_workload_lease(
            owner="host-b", generation=GEN_B, lease_duration=timedelta(seconds=30)
        )


def test_expired_lease_is_reclaimed_and_old_host_cannot_renew_or_release() -> None:
    first = PostgresPaperWorkerRepository(SessionFactory, now=lambda: NOW)
    first.current()
    first.acquire_workload_lease(
        owner="host-a", generation=GEN_A, lease_duration=timedelta(seconds=30)
    )
    later = NOW + timedelta(seconds=31)
    second = PostgresPaperWorkerRepository(SessionFactory, now=lambda: later)
    second.acquire_workload_lease(
        owner="host-b", generation=GEN_B, lease_duration=timedelta(seconds=30)
    )

    stale = PostgresPaperWorkerRepository(SessionFactory, now=lambda: later)
    with pytest.raises(WorkloadLeaseFencedError, match="generation is fenced"):
        stale.renew_workload_lease(
            owner="host-a", generation=GEN_A, lease_duration=timedelta(seconds=30)
        )
    with pytest.raises(WorkloadLeaseFencedError, match="release is fenced"):
        stale.release_workload_lease(owner="host-a", generation=GEN_A)

    second.release_workload_lease(owner="host-b", generation=GEN_B)
    with SessionFactory() as session:
        row = session.execute(
            text(
                "SELECT active_generation, lease_owner, lease_expires_at "
                "FROM quantora.paper_worker_states WHERE id = 'paper'"
            )
        ).one()
    assert tuple(row) == (None, None, None)
