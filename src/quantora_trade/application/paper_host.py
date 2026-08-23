"""Explicit, process-local host for the bounded PAPER workload.

Importing this module never starts work. A deployment process must consume an
authorized durable command and then call ``poll_once`` explicitly.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from quantora_trade.application.paper_operations import (
    CurrentMarketInput,
    CycleReport,
    PaperCycleRunner,
)
from quantora_trade.application.paper_worker import PaperWorkerConfig


class CurrentMarketInputProvider(Protocol):
    def current(self, *, symbol: str, timeframe: str) -> CurrentMarketInput | None: ...


class WorkloadLeaseRepository(Protocol):
    """Durable singleton lease; command-queue leases are intentionally separate."""

    def acquire_workload_lease(
        self, *, owner: str, generation: UUID, lease_duration: timedelta
    ) -> None: ...

    def renew_workload_lease(
        self, *, owner: str, generation: UUID, lease_duration: timedelta
    ) -> None: ...

    def release_workload_lease(self, *, owner: str, generation: UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class HostPollReport:
    markets_polled: int
    attempted: int


class PaperWorkloadHost:
    """PAPER-only host with an active-generation fencing token.

    Each call is bounded by configured symbols/timeframes and ``max_workload``.
    There is deliberately no background thread, scheduler, or automatic restart.
    """

    def __init__(
        self,
        *,
        runner: PaperCycleRunner,
        market: CurrentMarketInputProvider,
        leases: WorkloadLeaseRepository,
        owner: str,
        lease_duration: timedelta,
        max_workload_per_market: int = 1,
    ) -> None:
        if not 1 <= max_workload_per_market <= 100:
            raise ValueError("max_workload_per_market must be between 1 and 100")
        if not owner.strip() or owner != owner.strip():
            raise ValueError("owner must be non-empty and trimmed")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self._runner = runner
        self._market = market
        self._leases = leases
        self._owner = owner
        self._lease_duration = lease_duration
        self._max_workload = max_workload_per_market
        self._config: PaperWorkerConfig | None = None
        self._generation: UUID | None = None

    @property
    def running(self) -> bool:
        return self._generation is not None

    @property
    def active_generation(self) -> UUID | None:
        """Return the locally held generation; each use is still durably renewed."""

        return self._generation

    def start(self, config: PaperWorkerConfig, *, fence_token: UUID) -> None:
        if self._generation == fence_token and self._config == config:
            self._renew(fence_token)
            return
        if self._generation is not None:
            raise RuntimeError("PAPER workload is already owned by another generation")
        self._leases.acquire_workload_lease(
            owner=self._owner,
            generation=fence_token,
            lease_duration=self._lease_duration,
        )
        self._config = config
        self._generation = fence_token

    def stop(self, *, fence_token: UUID) -> None:
        if self._generation is None:
            return
        if not isinstance(fence_token, UUID):
            raise TypeError("fence_token must be a UUID")
        if fence_token != self._generation:
            raise PermissionError("PAPER workload generation is fenced")
        generation = self._generation
        self._leases.release_workload_lease(owner=self._owner, generation=generation)
        self._generation = None
        self._config = None

    def poll_once(self, *, fence_token: UUID) -> HostPollReport:
        if self._generation is None or self._config is None:
            raise RuntimeError("PAPER workload is stopped")
        if fence_token != self._generation:
            raise PermissionError("PAPER workload generation is fenced")
        self._renew(fence_token)
        polled = attempted = 0
        for symbol in self._config.symbols:
            for timeframe in self._config.timeframes:
                # Recheck the generation at every boundary so a controlled stop
                # between markets prevents further work.
                if fence_token != self._generation:
                    raise PermissionError("PAPER workload generation is fenced")
                self._renew(fence_token)
                observation = self._market.current(symbol=symbol, timeframe=timeframe)
                polled += 1
                if observation is None:
                    continue
                # This is the final durable fence immediately before the order-
                # producing cycle side effect. A reclaimed generation cannot pass.
                self._renew(fence_token)
                report: CycleReport = self._runner.run_cycle(
                    observation, max_workload=self._max_workload
                )
                attempted += report.attempted
        return HostPollReport(markets_polled=polled, attempted=attempted)

    def _renew(self, generation: UUID) -> None:
        self._leases.renew_workload_lease(
            owner=self._owner,
            generation=generation,
            lease_duration=self._lease_duration,
        )


__all__ = [
    "CurrentMarketInputProvider",
    "HostPollReport",
    "PaperWorkloadHost",
    "WorkloadLeaseRepository",
]
