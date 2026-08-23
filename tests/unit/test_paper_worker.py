from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quantora_trade.application.paper_worker import (
    PaperWorkerConfig,
    PaperWorkerControl,
    PaperWorkerState,
    StartPaperWorker,
    StoredWorkerCommand,
    WorkerStatus,
)
from quantora_trade.domain.enums import TradingMode

NOW = datetime(2026, 8, 23, 4, tzinfo=UTC)


@dataclass
class FixedClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


@dataclass
class Gate:
    blocked: bool = False
    broken: bool = False

    def new_entries_blocked(self) -> bool:
        if self.broken:
            raise RuntimeError("repository unavailable")
        return self.blocked


class FakeRepository:
    def __init__(self) -> None:
        self.state = PaperWorkerState(WorkerStatus.STOPPED, NOW)
        self.commands: dict[str, StoredWorkerCommand] = {}

    def current(self) -> PaperWorkerState:
        return self.state

    def command(self, command_id: str) -> StoredWorkerCommand | None:
        return self.commands.get(command_id)

    def persist(
        self, *, expected: PaperWorkerState, command: StoredWorkerCommand
    ) -> PaperWorkerState:
        if self.state != expected:
            raise RuntimeError("optimistic concurrency conflict")
        self.commands[command.command_id] = command
        self.state = command.result
        return self.state


@pytest.fixture
def config() -> PaperWorkerConfig:
    return PaperWorkerConfig(
        account="paper-primary",
        strategy_id="trend-v1",
        symbols=("XAUUSD", "EURUSD"),
        timeframes=("M5", "H1"),
        polling_interval_seconds=Decimal("5"),
    )


def control(gate: Gate | None = None) -> tuple[PaperWorkerControl, FakeRepository]:
    repository = FakeRepository()
    return (
        PaperWorkerControl(
            repository=repository,
            clock=FixedClock(),
            entry_gate=gate or Gate(),
        ),
        repository,
    )


def test_start_is_paper_only_and_captures_immutable_config(config: PaperWorkerConfig) -> None:
    with pytest.raises(PermissionError, match="PAPER mode only"):
        StartPaperWorker("live", TradingMode.LIVE, config)

    worker, _ = control()
    state = worker.start(StartPaperWorker("start-1", TradingMode.PAPER, config))

    assert state.status is WorkerStatus.STARTING
    assert state.config is config
    assert state.config_hash == config.digest


def test_duplicate_command_is_idempotent_but_collision_is_rejected(
    config: PaperWorkerConfig,
) -> None:
    worker, repository = control()
    request = StartPaperWorker("command-1", TradingMode.PAPER, config)

    first = worker.start(request)
    second = worker.start(request)

    assert second is first
    assert len(repository.commands) == 1
    with pytest.raises(ValueError, match="different command"):
        worker.started("command-1")


def test_duplicate_start_replays_after_kill_switch_activation(
    config: PaperWorkerConfig,
) -> None:
    gate = Gate()
    worker, _ = control(gate)
    request = StartPaperWorker("command-1", TradingMode.PAPER, config)
    first = worker.start(request)

    gate.blocked = True

    assert worker.start(request) is first


def test_legal_start_stop_and_recover_transitions(config: PaperWorkerConfig) -> None:
    worker, _ = control()

    assert (
        worker.start(StartPaperWorker("1", TradingMode.PAPER, config)).status
        is WorkerStatus.STARTING
    )
    assert worker.started("2").status is WorkerStatus.RUNNING
    assert worker.stop("3").status is WorkerStatus.STOPPING
    assert worker.stopped("4").status is WorkerStatus.STOPPED
    with pytest.raises(ValueError, match="illegal worker transition"):
        worker.stop("5")


def test_degraded_and_halted_recover_to_stopped_without_restart(
    config: PaperWorkerConfig,
) -> None:
    worker, _ = control()
    worker.start(StartPaperWorker("1", TradingMode.PAPER, config))
    worker.started("2")

    assert worker.degrade("3", "data stale").status is WorkerStatus.DEGRADED
    assert not worker.allows_new_entries()
    assert worker.recover("4", "feed healthy").status is WorkerStatus.STOPPED
    with pytest.raises(ValueError, match="illegal worker transition"):
        worker.started("5")

    assert worker.halt("6", "operator halt").status is WorkerStatus.HALTED
    assert worker.recover("7", "incident closed").status is WorkerStatus.STOPPED


def test_kill_switch_fails_closed_but_safety_maintenance_continues(
    config: PaperWorkerConfig,
) -> None:
    gate = Gate(blocked=True)
    worker, repository = control(gate)

    with pytest.raises(PermissionError, match="kill switch"):
        worker.start(StartPaperWorker("1", TradingMode.PAPER, config))
    assert repository.state.status is WorkerStatus.STOPPED

    called: list[str] = []
    worker.run_safety_maintenance(lambda: called.append("reconciled"))
    assert called == ["reconciled"]

    gate.blocked = False
    worker.start(StartPaperWorker("2", TradingMode.PAPER, config))
    worker.started("3")
    assert worker.allows_new_entries()
    gate.broken = True
    assert not worker.allows_new_entries()
    worker.run_safety_maintenance(lambda: called.append("protected"))
    assert called == ["reconciled", "protected"]


@pytest.mark.parametrize(
    ("status", "operation"),
    [
        (WorkerStatus.STOPPED, "started"),
        (WorkerStatus.RUNNING, "started"),
        (WorkerStatus.HALTED, "stop"),
        (WorkerStatus.STARTING, "recover"),
    ],
)
def test_illegal_transitions_fail_without_persistence(status: WorkerStatus, operation: str) -> None:
    worker, repository = control()
    repository.state = PaperWorkerState(status, NOW)

    with pytest.raises(ValueError, match="illegal worker transition"):
        if operation == "recover":
            worker.recover("bad", "not allowed")
        else:
            getattr(worker, operation)("bad")
    assert repository.commands == {}


def test_configuration_validation_and_hash_stability(config: PaperWorkerConfig) -> None:
    same = PaperWorkerConfig(
        account="paper-primary",
        strategy_id="trend-v1",
        symbols=("XAUUSD", "EURUSD"),
        timeframes=("M5", "H1"),
        polling_interval_seconds=Decimal("5"),
    )
    assert same.digest == config.digest

    with pytest.raises(ValueError, match="canonical uppercase"):
        PaperWorkerConfig("a", "s", ("xauusd",), ("M5",), Decimal("1"))
    with pytest.raises(ValueError, match="greater than zero"):
        PaperWorkerConfig("a", "s", ("XAUUSD",), ("M5",), Decimal("0"))
