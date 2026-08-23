from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

from quantora_trade.application.paper_command_consumer import (
    PaperCommandConsumer,
    PaperRuntimeDefaults,
)
from quantora_trade.application.paper_worker import (
    PaperWorkerConfig,
    PaperWorkerControl,
    PaperWorkerState,
    StoredWorkerCommand,
    WorkerStatus,
)
from quantora_trade.infrastructure.database.command_repository import (
    CommandStatus,
    DurableSystemCommand,
)

NOW = datetime(2026, 8, 23, tzinfo=UTC)
TOKEN = UUID("00000000-0000-0000-0000-000000000777")


class Repository:
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
        assert expected == self.state
        self.commands[command.command_id] = command
        self.state = command.result
        return self.state


@dataclass
class Gate:
    blocked: bool = False

    def new_entries_blocked(self) -> bool:
        return self.blocked


class Queue:
    def __init__(self, command: DurableSystemCommand | None) -> None:
        self.command = command
        self.acked: dict[str, object] | None = None
        self.failed: dict[str, object] | None = None
        self.heartbeats = 0

    def claim(self, **_: object) -> DurableSystemCommand | None:
        result, self.command = self.command, None
        return result

    def acknowledge(self, _id: UUID, **kwargs: object) -> DurableSystemCommand:
        assert kwargs["lease_token"] == TOKEN
        self.acked = cast(dict[str, object], kwargs["result"])
        return cast(DurableSystemCommand, object())

    def heartbeat(self, _id: UUID, **kwargs: object) -> DurableSystemCommand:
        assert kwargs["lease_token"] == TOKEN
        self.heartbeats += 1
        return cast(DurableSystemCommand, object())

    def fail(self, _id: UUID, **kwargs: object) -> DurableSystemCommand:
        assert kwargs["lease_token"] == TOKEN
        self.failed = cast(dict[str, object], kwargs["result"])
        return cast(DurableSystemCommand, object())


class Workload:
    def __init__(self, *, fail_start: bool = False, fail_stop: bool = False) -> None:
        self.started: PaperWorkerConfig | None = None
        self.stopped = False
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.generation: UUID | None = None

    def start(self, config: PaperWorkerConfig, *, fence_token: UUID) -> None:
        assert fence_token != TOKEN
        self.generation = fence_token
        if self.fail_start:
            raise RuntimeError("boom")
        self.started = config

    @property
    def active_generation(self) -> UUID | None:
        return self.generation

    def stop(self, *, fence_token: UUID) -> None:
        assert fence_token == self.generation
        if self.fail_stop:
            raise RuntimeError("stop failed")
        self.stopped = True


def command(action: str = "start", mode: str = "paper") -> DurableSystemCommand:
    return DurableSystemCommand(
        id=UUID("00000000-0000-0000-0000-000000000006"),
        request_id="request-1",
        idempotency_key="key-1",
        request_hash="a" * 64,
        action=action,
        mode=mode,
        payload={"strategy_id": "trend-v1", "symbols": ["XAUUSD", "EURUSD"]},
        actor="owner",
        status=CommandStatus.PROCESSING,
        created_at=NOW,
        updated_at=NOW,
        worker_id="runtime-1",
        lease_token=TOKEN,
        lease_expires_at=NOW + timedelta(minutes=1),
    )


def consumer(
    queue: Queue, gate: Gate | None = None, workload: Workload | None = None
) -> tuple[PaperCommandConsumer, Repository]:
    repository = Repository()
    control = PaperWorkerControl(
        repository=repository,
        clock=type("Clock", (), {"now": lambda self: NOW})(),
        entry_gate=gate or Gate(),
    )
    return (
        PaperCommandConsumer(
            queue=queue,
            control=control,
            defaults=PaperRuntimeDefaults("paper-primary", ("M5",), Decimal("5")),
            worker_id="runtime-1",
            lease_duration=timedelta(seconds=30),
            workload=workload or Workload(),
        ),
        repository,
    )


def test_consumer_starts_paper_worker_and_acknowledges_fenced_command() -> None:
    queue = Queue(command())
    runtime, repository = consumer(queue)

    assert runtime.run_once() is True
    assert repository.state.status is WorkerStatus.RUNNING
    assert repository.state.config is not None
    assert repository.state.config.symbols == ("XAUUSD", "EURUSD")
    assert queue.acked == {
        "status": "running",
        "config_hash": repository.state.config_hash,
        "worker_id": "runtime-1",
    }
    assert queue.failed is None
    assert queue.heartbeats == 1


def test_consumer_stops_worker_and_never_submits_orders() -> None:
    start_queue = Queue(command())
    runtime, repository = consumer(start_queue)
    runtime.run_once()
    stop_queue = Queue(command("stop"))
    runtime._queue = stop_queue  # type: ignore[attr-defined]

    assert runtime.run_once() is True
    assert repository.state.status is WorkerStatus.STOPPED
    assert stop_queue.acked is not None


def test_live_or_kill_switch_command_fails_closed() -> None:
    for rejected, gate in ((command(mode="live"), Gate()), (command(), Gate(blocked=True))):
        queue = Queue(rejected)
        runtime, repository = consumer(queue, gate)
        assert runtime.run_once() is True
        assert repository.state.status is WorkerStatus.STOPPED
        assert queue.acked is None
        assert queue.failed is not None
        assert queue.failed["code"] == "COMMAND_REJECTED"


def test_empty_queue_is_a_noop() -> None:
    queue = Queue(None)
    runtime, _ = consumer(queue)
    assert runtime.run_once() is False
    assert queue.acked is None and queue.failed is None


def test_start_fails_closed_when_workload_is_unconfigured() -> None:
    queue = Queue(command())
    runtime, repository = consumer(queue)
    runtime._workload = None  # type: ignore[attr-defined]
    assert runtime.run_once() is True
    assert repository.state.status is WorkerStatus.STOPPED
    assert queue.failed is not None


def test_running_is_not_persisted_until_workload_really_starts() -> None:
    queue = Queue(command())
    runtime, repository = consumer(queue, workload=Workload(fail_start=True))
    assert runtime.run_once() is True
    assert repository.state.status is WorkerStatus.DEGRADED
    assert queue.acked is None and queue.failed is not None


def test_stop_failure_is_auditable_halted_and_recoverable() -> None:
    workload = Workload(fail_stop=True)
    start_queue = Queue(command())
    runtime, repository = consumer(start_queue, workload=workload)
    assert runtime.run_once() is True
    stop_queue = Queue(command("stop"))
    runtime._queue = stop_queue  # type: ignore[attr-defined]

    assert runtime.run_once() is True
    assert repository.state.status is WorkerStatus.HALTED
    assert repository.state.reason == "workload stop failed"
    assert stop_queue.acked is None and stop_queue.failed is not None
