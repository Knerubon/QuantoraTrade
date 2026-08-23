"""Durable command consumer for PAPER worker lifecycle control only.

This module has no scheduler, strategy, broker, or order-submission dependency.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid5

from quantora_trade.application.paper_worker import (
    PaperWorkerConfig,
    PaperWorkerControl,
    StartPaperWorker,
)
from quantora_trade.domain.enums import TradingMode
from quantora_trade.infrastructure.database.command_repository import DurableSystemCommand

_WORKLOAD_GENERATION_NAMESPACE = UUID("d5844302-ce09-4f0c-9a03-b6736366f320")


def workload_generation_for_command(command_id: UUID) -> UUID:
    """Derive the stable durable workload generation owned by a START command."""

    return uuid5(_WORKLOAD_GENERATION_NAMESPACE, str(command_id))


class CommandQueue(Protocol):
    def claim(
        self, *, worker_id: str, lease_duration: timedelta
    ) -> DurableSystemCommand | None: ...

    def acknowledge(
        self,
        command_id: UUID,
        *,
        worker_id: str,
        lease_token: UUID,
        result: Mapping[str, object],
    ) -> DurableSystemCommand: ...

    def heartbeat(
        self,
        command_id: UUID,
        *,
        worker_id: str,
        lease_token: UUID,
        lease_duration: timedelta,
    ) -> DurableSystemCommand: ...

    def fail(
        self,
        command_id: UUID,
        *,
        worker_id: str,
        lease_token: UUID,
        result: Mapping[str, object],
    ) -> DurableSystemCommand: ...


class PaperWorkloadLifecycle(Protocol):
    """Actual strategy/feed workload controlled separately from durable state."""

    def start(self, config: PaperWorkerConfig, *, fence_token: UUID) -> None: ...

    def stop(self, *, fence_token: UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class PaperRuntimeDefaults:
    account: str
    timeframes: tuple[str, ...]
    polling_interval_seconds: Decimal

    def __post_init__(self) -> None:
        # Reuse the domain validation with inert canonical placeholders.
        PaperWorkerConfig(
            account=self.account,
            strategy_id="validation",
            symbols=("VALIDATION",),
            timeframes=self.timeframes,
            polling_interval_seconds=self.polling_interval_seconds,
        )


class PaperCommandConsumer:
    """Claim one fenced command and atomically finish it through the queue port."""

    def __init__(
        self,
        *,
        queue: CommandQueue,
        control: PaperWorkerControl,
        defaults: PaperRuntimeDefaults,
        worker_id: str,
        lease_duration: timedelta,
        workload: PaperWorkloadLifecycle | None = None,
    ) -> None:
        if not worker_id.strip() or worker_id != worker_id.strip():
            raise ValueError("worker_id must be non-empty and trimmed")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self._queue = queue
        self._control = control
        self._defaults = defaults
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._workload = workload

    def run_once(self) -> bool:
        command = self._queue.claim(worker_id=self._worker_id, lease_duration=self._lease_duration)
        if command is None:
            return False
        if command.lease_token is None:
            raise RuntimeError("claimed command is missing its fencing token")
        try:
            # Renew immediately before side effects. The repository rejects an
            # expired/stolen lease, fencing stale consumers before host control.
            self._queue.heartbeat(
                command.id,
                worker_id=self._worker_id,
                lease_token=command.lease_token,
                lease_duration=self._lease_duration,
            )
            result = self._execute(command)
        except Exception as exc:
            self._queue.fail(
                command.id,
                worker_id=self._worker_id,
                lease_token=command.lease_token,
                result={"code": "COMMAND_REJECTED", "error_type": type(exc).__name__},
            )
            return True
        self._queue.acknowledge(
            command.id,
            worker_id=self._worker_id,
            lease_token=command.lease_token,
            result=result,
        )
        return True

    def _execute(self, command: DurableSystemCommand) -> dict[str, object]:
        if command.mode != TradingMode.PAPER.value:
            raise PermissionError("worker command must be PAPER mode")
        if command.lease_token is None:
            raise RuntimeError("claimed command is missing its fencing token")
        # Workload ownership is durable and intentionally independent from the
        # short-lived command-processing lease. Determinism makes a replay of
        # the same START command idempotently reacquire the same generation.
        fence_token = workload_generation_for_command(command.id)
        base_id = str(command.id)
        if command.action == "start":
            if self._workload is None:
                raise RuntimeError("paper workload lifecycle is not configured")
            config = self._config(command.payload)
            self._control.start(StartPaperWorker(f"{base_id}:start", TradingMode.PAPER, config))
            try:
                self._workload.start(config, fence_token=fence_token)
            except Exception:
                self._control.degrade(f"{base_id}:workload-failed", "workload start failed")
                raise
            state = self._control.started(f"{base_id}:started")
        elif command.action == "stop":
            if self._workload is None:
                raise RuntimeError("paper workload lifecycle is not configured")
            self._control.stop(f"{base_id}:stop")
            try:
                self._workload.stop(fence_token=fence_token)
            except Exception:
                self._control.halt(f"{base_id}:stop-failed", "workload stop failed")
                raise
            state = self._control.stopped(f"{base_id}:stopped")
        else:
            raise ValueError("unsupported worker command action")
        return {
            "status": state.status.value,
            "config_hash": state.config_hash,
            "worker_id": self._worker_id,
        }

    def _config(self, payload: Mapping[str, object]) -> PaperWorkerConfig:
        strategy_id = payload.get("strategy_id")
        symbols = payload.get("symbols")
        if not isinstance(strategy_id, str) or not isinstance(symbols, (list, tuple)):
            raise ValueError("start payload is missing typed strategy_id or symbols")
        if not all(isinstance(item, str) for item in symbols):
            raise ValueError("start payload symbols must be strings")
        return PaperWorkerConfig(
            account=self._defaults.account,
            strategy_id=strategy_id,
            symbols=tuple(symbols),
            timeframes=self._defaults.timeframes,
            polling_interval_seconds=self._defaults.polling_interval_seconds,
        )


__all__ = ["PaperCommandConsumer", "PaperRuntimeDefaults", "PaperWorkloadLifecycle"]
