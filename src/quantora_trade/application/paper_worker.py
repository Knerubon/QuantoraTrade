"""Durable PAPER-worker control state machine.

This module controls lifecycle only.  It deliberately contains no scheduler,
trading loop, network client, broker adapter, or order-submission capability.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from quantora_trade.domain.enums import TradingMode
from quantora_trade.domain.ports import ClockPort


class WorkerStatus(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    DEGRADED = "degraded"
    HALTED = "halted"


@dataclass(frozen=True, slots=True)
class PaperWorkerConfig:
    """Validated, immutable configuration copied from an API/FE request."""

    account: str
    strategy_id: str
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    polling_interval_seconds: Decimal

    def __post_init__(self) -> None:
        for name in ("account", "strategy_id"):
            value = getattr(self, name)
            if not value.strip() or value != value.strip():
                raise ValueError(f"{name} must be non-empty and trimmed")
        if not self.symbols or any(
            not value or value != value.strip().upper() for value in self.symbols
        ):
            raise ValueError("symbols must be non-empty canonical uppercase values")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be unique")
        if not self.timeframes or any(
            not value.strip() or value != value.strip().upper() for value in self.timeframes
        ):
            raise ValueError("timeframes must be non-empty canonical uppercase values")
        if len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError("timeframes must be unique")
        if not self.polling_interval_seconds.is_finite() or self.polling_interval_seconds <= 0:
            raise ValueError("polling_interval_seconds must be finite and greater than zero")

    @property
    def digest(self) -> str:
        payload = json.dumps(
            {
                "account": self.account,
                "polling_interval_seconds": str(self.polling_interval_seconds),
                "strategy_id": self.strategy_id,
                "symbols": self.symbols,
                "timeframes": self.timeframes,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class StartPaperWorker:
    command_id: str
    mode: TradingMode
    config: PaperWorkerConfig

    def __post_init__(self) -> None:
        _command_id(self.command_id)
        if self.mode is not TradingMode.PAPER:
            raise PermissionError("paper worker accepts PAPER mode only")


@dataclass(frozen=True, slots=True)
class PaperWorkerState:
    status: WorkerStatus
    changed_at: datetime
    config: PaperWorkerConfig | None = None
    config_hash: str | None = None
    reason: str | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        _utc(self.changed_at)
        if (self.config is None) != (self.config_hash is None):
            raise ValueError("config and config_hash must be stored together")
        if self.config is not None and self.config.digest != self.config_hash:
            raise ValueError("config_hash does not match immutable config snapshot")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("reason must not be blank")
        if self.revision < 0:
            raise ValueError("revision must be nonnegative")


@dataclass(frozen=True, slots=True)
class StoredWorkerCommand:
    command_id: str
    fingerprint: str
    result: PaperWorkerState


class PaperWorkerRepository(Protocol):
    """Persistence port; implementations must atomically store command + transition."""

    def current(self) -> PaperWorkerState: ...

    def command(self, command_id: str) -> StoredWorkerCommand | None: ...

    def persist(
        self,
        *,
        expected: PaperWorkerState,
        command: StoredWorkerCommand,
    ) -> PaperWorkerState: ...


class NewEntryGate(Protocol):
    """Fail-closed gate backed by the durable kill-switch service."""

    def new_entries_blocked(self) -> bool: ...


class PaperWorkerControl:
    """Idempotent lifecycle control over a durable repository."""

    def __init__(
        self,
        *,
        repository: PaperWorkerRepository,
        clock: ClockPort,
        entry_gate: NewEntryGate,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._entry_gate = entry_gate

    def start(self, request: StartPaperWorker) -> PaperWorkerState:
        return self._transition(
            request.command_id,
            f"start:{request.mode.value}:{request.config.digest}",
            {WorkerStatus.STOPPED},
            WorkerStatus.STARTING,
            config=request.config,
            require_entries_enabled=True,
        )

    def started(self, command_id: str) -> PaperWorkerState:
        return self._simple(command_id, "started", {WorkerStatus.STARTING}, WorkerStatus.RUNNING)

    def stop(self, command_id: str) -> PaperWorkerState:
        return self._simple(
            command_id,
            "stop",
            {WorkerStatus.STARTING, WorkerStatus.RUNNING, WorkerStatus.DEGRADED},
            WorkerStatus.STOPPING,
        )

    def stopped(self, command_id: str) -> PaperWorkerState:
        return self._simple(command_id, "stopped", {WorkerStatus.STOPPING}, WorkerStatus.STOPPED)

    def degrade(self, command_id: str, reason: str) -> PaperWorkerState:
        if not reason.strip():
            raise ValueError("degraded reason must not be blank")
        return self._transition(
            command_id,
            f"degrade:{reason}",
            {WorkerStatus.STARTING, WorkerStatus.RUNNING},
            WorkerStatus.DEGRADED,
            reason=reason,
        )

    def halt(self, command_id: str, reason: str) -> PaperWorkerState:
        if not reason.strip():
            raise ValueError("halt reason must not be blank")
        return self._transition(
            command_id,
            f"halt:{reason}",
            set(WorkerStatus) - {WorkerStatus.HALTED},
            WorkerStatus.HALTED,
            reason=reason,
        )

    def recover(self, command_id: str, reason: str) -> PaperWorkerState:
        """Recover to STOPPED; recovery never restarts trading automatically."""

        if not reason.strip():
            raise ValueError("recovery reason must not be blank")
        return self._transition(
            command_id,
            f"recover:{reason}",
            {WorkerStatus.DEGRADED, WorkerStatus.HALTED},
            WorkerStatus.STOPPED,
            reason=reason,
        )

    def allows_new_entries(self) -> bool:
        state = self._repository.current()
        return state.status is WorkerStatus.RUNNING and not self._blocked()

    def run_safety_maintenance(self, callback: Callable[[], None]) -> None:
        """Allow protection/reconciliation even while entries are blocked or halted."""

        callback()

    def _simple(
        self,
        command_id: str,
        operation: str,
        allowed: set[WorkerStatus],
        target: WorkerStatus,
    ) -> PaperWorkerState:
        return self._transition(command_id, operation, allowed, target)

    def _transition(
        self,
        command_id: str,
        fingerprint: str,
        allowed: set[WorkerStatus],
        target: WorkerStatus,
        *,
        config: PaperWorkerConfig | None = None,
        reason: str | None = None,
        require_entries_enabled: bool = False,
    ) -> PaperWorkerState:
        _command_id(command_id)
        replay = self._repository.command(command_id)
        if replay is not None:
            if replay.fingerprint != fingerprint:
                raise ValueError("command_id reused for a different command")
            return replay.result
        if require_entries_enabled and self._blocked():
            raise PermissionError("kill switch blocks paper-worker start")
        current = self._repository.current()
        if current.status not in allowed:
            raise ValueError(f"illegal worker transition: {current.status.value} -> {target.value}")
        snapshot = config if config is not None else current.config
        result = PaperWorkerState(
            status=target,
            changed_at=self._clock.now(),
            config=snapshot,
            config_hash=snapshot.digest if snapshot is not None else None,
            reason=reason,
            revision=current.revision + 1,
        )
        stored = StoredWorkerCommand(command_id, fingerprint, result)
        return self._repository.persist(expected=current, command=stored)

    def _blocked(self) -> bool:
        try:
            return self._entry_gate.new_entries_blocked()
        except Exception:
            return True


def _command_id(value: str) -> None:
    if not value.strip() or value != value.strip():
        raise ValueError("command_id must be non-empty and trimmed")


def _utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("changed_at must be timezone-aware UTC")
