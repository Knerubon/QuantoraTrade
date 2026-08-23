from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from quantora_trade.application.paper_host import PaperWorkloadHost
from quantora_trade.application.paper_operations import CurrentMarketInput, CycleReport
from quantora_trade.application.paper_worker import PaperWorkerConfig

NOW = datetime(2026, 8, 23, tzinfo=UTC)
TOKEN = UUID("00000000-0000-0000-0000-000000000111")
STALE = UUID("00000000-0000-0000-0000-000000000222")


class DurableLease:
    def __init__(self) -> None:
        self.now = NOW
        self.owner: str | None = None
        self.generation: UUID | None = None
        self.expires: datetime | None = None

    def acquire_workload_lease(
        self, *, owner: str, generation: UUID, lease_duration: timedelta
    ) -> None:
        if (
            self.generation is not None
            and self.expires is not None
            and self.expires > self.now
            and (self.owner, self.generation) != (owner, generation)
        ):
            raise PermissionError("owned")
        self.owner, self.generation, self.expires = owner, generation, self.now + lease_duration

    def renew_workload_lease(
        self, *, owner: str, generation: UUID, lease_duration: timedelta
    ) -> None:
        if (
            (self.owner, self.generation) != (owner, generation)
            or not self.expires
            or self.expires <= self.now
        ):
            raise PermissionError("fenced")
        self.expires = self.now + lease_duration

    def release_workload_lease(self, *, owner: str, generation: UUID) -> None:
        if (
            (self.owner, self.generation) != (owner, generation)
            or not self.expires
            or self.expires <= self.now
        ):
            raise PermissionError("fenced")
        self.owner = None
        self.generation = None
        self.expires = None


class Market:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def current(self, *, symbol: str, timeframe: str) -> CurrentMarketInput:
        self.calls.append((symbol, timeframe))
        return CurrentMarketInput(symbol, NOW)


class Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def run_cycle(self, market: CurrentMarketInput, *, max_workload: int) -> CycleReport:
        self.calls.append((market.symbol, max_workload))
        return CycleReport(1, ())


def config() -> PaperWorkerConfig:
    return PaperWorkerConfig(
        "paper-primary",
        "trend-v1",
        ("XAUUSD", "EURUSD"),
        ("M5", "M15"),
        Decimal("5"),
    )


def test_host_is_explicit_bounded_idempotent_and_paper_only() -> None:
    market, runner = Market(), Runner()
    host = PaperWorkloadHost(  # type: ignore[arg-type]
        runner=runner,
        market=market,
        leases=DurableLease(),
        owner="host-a",
        lease_duration=timedelta(seconds=30),
        max_workload_per_market=2,
    )
    assert host.running is False
    with pytest.raises(RuntimeError, match="stopped"):
        host.poll_once(fence_token=TOKEN)

    host.start(config(), fence_token=TOKEN)
    host.start(config(), fence_token=TOKEN)
    report = host.poll_once(fence_token=TOKEN)

    assert report.markets_polled == 4
    assert report.attempted == 4
    assert len(runner.calls) == 4
    assert all(limit == 2 for _, limit in runner.calls)
    with pytest.raises(RuntimeError, match="another generation"):
        host.start(config(), fence_token=STALE)

    with pytest.raises(PermissionError, match="fenced"):
        host.stop(fence_token=STALE)
    host.stop(fence_token=TOKEN)
    host.stop(fence_token=STALE)
    assert host.running is False


def test_stale_generation_cannot_poll_or_duplicate_side_effects() -> None:
    market, runner = Market(), Runner()
    host = PaperWorkloadHost(  # type: ignore[arg-type]
        runner=runner,
        market=market,
        leases=DurableLease(),
        owner="host-a",
        lease_duration=timedelta(seconds=30),
    )
    host.start(config(), fence_token=TOKEN)

    with pytest.raises(PermissionError, match="fenced"):
        host.poll_once(fence_token=STALE)
    assert market.calls == []
    assert runner.calls == []


def test_two_hosts_expiry_reclaim_fences_old_host_before_side_effect() -> None:
    lease, old_market, old_runner = DurableLease(), Market(), Runner()
    old = PaperWorkloadHost(  # type: ignore[arg-type]
        runner=old_runner,
        market=old_market,
        leases=lease,
        owner="host-a",
        lease_duration=timedelta(seconds=30),
    )
    old.start(config(), fence_token=TOKEN)

    new = PaperWorkloadHost(  # type: ignore[arg-type]
        runner=Runner(),
        market=Market(),
        leases=lease,
        owner="host-b",
        lease_duration=timedelta(seconds=30),
    )
    with pytest.raises(PermissionError):
        new.start(config(), fence_token=STALE)

    lease.now += timedelta(seconds=31)
    new.start(config(), fence_token=STALE)
    with pytest.raises(PermissionError, match="fenced"):
        old.poll_once(fence_token=TOKEN)
    assert old_market.calls == []
    assert old_runner.calls == []


def test_restart_can_reacquire_same_generation_idempotently() -> None:
    lease = DurableLease()
    first = PaperWorkloadHost(  # type: ignore[arg-type]
        runner=Runner(),
        market=Market(),
        leases=lease,
        owner="host-a",
        lease_duration=timedelta(seconds=30),
    )
    first.start(config(), fence_token=TOKEN)
    restarted = PaperWorkloadHost(  # type: ignore[arg-type]
        runner=Runner(),
        market=Market(),
        leases=lease,
        owner="host-a",
        lease_duration=timedelta(seconds=30),
    )
    restarted.start(config(), fence_token=TOKEN)
    assert restarted.running is True
