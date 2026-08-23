from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from quantora_trade.api import create_app
from quantora_trade.api.schemas import ResolvedSymbolSpecification
from quantora_trade.application.paper_command_consumer import (
    PaperCommandConsumer,
    PaperRuntimeDefaults,
)
from quantora_trade.application.paper_host import PaperWorkloadHost
from quantora_trade.application.paper_operations import CurrentMarketInput, CycleReport
from quantora_trade.application.paper_worker import (
    PaperWorkerControl,
    PaperWorkerState,
    StoredWorkerCommand,
    WorkerStatus,
)
from quantora_trade.infrastructure.database.command_repository import (
    CommandStatus,
    DurableSystemCommand,
    EnqueueResult,
)

NOW = datetime(2026, 8, 23, tzinfo=UTC)
TOKEN = UUID("00000000-0000-0000-0000-000000000999")


class SymbolPreflight:
    def resolve(self, symbols: Sequence[str]) -> tuple[ResolvedSymbolSpecification, ...]:
        return tuple(
            ResolvedSymbolSpecification(
                symbol=symbol,
                specification_id=UUID(int=index + 1),
                specification_hash=f"{index + 1:064x}",
                quote_currency="USD",
            )
            for index, symbol in enumerate(symbols)
        )


class Queue:
    def __init__(self) -> None:
        self.item: DurableSystemCommand | None = None

    def enqueue(self, **values: object) -> EnqueueResult:
        self.item = DurableSystemCommand(
            id=uuid4(),
            request_id=cast(str, values["request_id"]),
            idempotency_key=cast(str, values["idempotency_key"]),
            request_hash=cast(str, values["request_hash"]),
            action=cast(str, values["action"]),
            mode=cast(str, values["mode"]),
            payload=cast(dict[str, object], values["payload"]),
            actor=cast(str, values["actor"]),
            status=CommandStatus.QUEUED,
            created_at=NOW,
            updated_at=NOW,
        )
        return EnqueueResult(self.item, True)

    def get(self, command_id: UUID) -> DurableSystemCommand | None:
        return self.item if self.item and self.item.id == command_id else None

    def claim(self, **_: object) -> DurableSystemCommand | None:
        if self.item is None or self.item.status is not CommandStatus.QUEUED:
            return None
        self.item = replace(
            self.item,
            status=CommandStatus.PROCESSING,
            worker_id="paper-1",
            lease_token=TOKEN,
            lease_expires_at=NOW + timedelta(minutes=1),
        )
        return self.item

    def heartbeat(self, command_id: UUID, **_: object) -> DurableSystemCommand:
        assert self.item is not None and self.item.id == command_id
        return self.item

    def acknowledge(self, command_id: UUID, **_: object) -> DurableSystemCommand:
        assert self.item is not None and self.item.id == command_id
        self.item = replace(self.item, status=CommandStatus.SUCCEEDED)
        return self.item

    def fail(self, command_id: UUID, **_: object) -> DurableSystemCommand:
        assert self.item is not None and self.item.id == command_id
        self.item = replace(self.item, status=CommandStatus.FAILED)
        return self.item


class WorkerRepository:
    def __init__(self) -> None:
        self.state = PaperWorkerState(WorkerStatus.STOPPED, NOW)
        self.commands: dict[str, StoredWorkerCommand] = {}
        self.generation: UUID | None = None

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

    def acquire_workload_lease(self, **kwargs: object) -> None:
        generation = kwargs["generation"]
        assert isinstance(generation, UUID)
        self.generation = generation

    def renew_workload_lease(self, **_: object) -> None:
        return None

    def release_workload_lease(self, **_: object) -> None:
        return None


class Safe:
    def new_entries_blocked(self) -> bool:
        return False


class Authorizer:
    def authorize(self, bearer_token: str, required_scope: str) -> str:
        assert bearer_token == "paper-token" and required_scope == "system:operate"
        return "owner"


class Market:
    def current(self, *, symbol: str, timeframe: str) -> CurrentMarketInput:
        assert timeframe == "M5"
        return CurrentMarketInput(symbol, NOW)


class Runner:
    def __init__(self) -> None:
        self.symbols: list[str] = []

    def run_cycle(self, market: CurrentMarketInput, *, max_workload: int) -> CycleReport:
        assert max_workload == 1
        self.symbols.append(market.symbol)
        return CycleReport(1, ())


def test_api_enqueue_consumer_host_and_bounded_runner_are_explicit() -> None:
    queue, worker_repository, runner = Queue(), WorkerRepository(), Runner()
    host = PaperWorkloadHost(
        runner=runner,  # type: ignore[arg-type]
        market=Market(),
        leases=worker_repository,
        owner="worker-1",
        lease_duration=timedelta(seconds=30),
    )
    control = PaperWorkerControl(
        repository=worker_repository,
        clock=type("Clock", (), {"now": lambda self: NOW})(),
        entry_gate=Safe(),
    )
    consumer = PaperCommandConsumer(
        queue=queue,
        control=control,
        defaults=PaperRuntimeDefaults("paper-primary", ("M5",), Decimal("1")),
        worker_id="paper-1",
        lease_duration=timedelta(seconds=30),
        workload=host,
    )
    client = TestClient(
        create_app(
            command_repository=queue,
            authorizer=Authorizer(),
            symbol_preflight=SymbolPreflight(),
        )
    )

    response = client.post(
        "/system/start",
        json={
            "mode": "paper",
            "symbols": ["XAUUSD", "EURUSD"],
            "strategy_id": "trend-v1",
            "reason": "owner requested",
        },
        headers={
            "Authorization": "Bearer paper-token",
            "X-Request-ID": "request-1",
            "Idempotency-Key": "start-1",
        },
    )

    assert response.status_code == 202
    assert host.running is False  # enqueue never executes inline
    assert consumer.run_once() is True
    assert worker_repository.state.status is WorkerStatus.RUNNING
    assert worker_repository.generation is not None
    assert worker_repository.generation != TOKEN
    assert host.poll_once(fence_token=worker_repository.generation).attempted == 2
    assert runner.symbols == ["XAUUSD", "EURUSD"]
