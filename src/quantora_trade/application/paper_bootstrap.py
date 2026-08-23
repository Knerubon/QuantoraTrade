"""Fail-closed construction of the explicit Phase 6 PAPER workload."""

from dataclasses import dataclass
from datetime import timedelta

from quantora_trade.application.paper_operations import (
    ApprovedIntentSource,
    CycleClock,
    FillAccountingPort,
    NewEntryGate,
    OperationalAuditPort,
    PaperCycleRunner,
    PaperEventProjector,
    ReadinessDependency,
)
from quantora_trade.execution.runtime import AlertPublisher, PaperRuntime
from quantora_trade.risk.submission import OrderSubmissionService


class Phase6CompositionError(RuntimeError):
    """Required safe PAPER dependencies were not supplied."""


@dataclass(frozen=True, slots=True)
class Phase6Components:
    runtime: PaperRuntime
    runner: PaperCycleRunner


def compose_phase6(
    *,
    submissions: OrderSubmissionService | None,
    source: ApprovedIntentSource | None,
    accounting: FillAccountingPort | None,
    audit: OperationalAuditPort | None,
    alerts: AlertPublisher | None,
    database: ReadinessDependency | None,
    authorization: ReadinessDependency | None,
    configuration: ReadinessDependency | None,
    entry_gate: NewEntryGate | None,
    clock: CycleClock | None,
    max_market_age: timedelta,
) -> Phase6Components:
    """Wire one PAPER runtime without starting it or exposing a Live path."""

    required = {
        "accounting": accounting,
        "alerts": alerts,
        "audit": audit,
        "authorization": authorization,
        "clock": clock,
        "configuration": configuration,
        "database": database,
        "entry_gate": entry_gate,
        "source": source,
        "submissions": submissions,
    }
    missing = tuple(sorted(name for name, value in required.items() if value is None))
    if missing:
        raise Phase6CompositionError(f"missing Phase 6 dependencies: {', '.join(missing)}")
    assert submissions is not None
    assert source is not None
    assert accounting is not None
    assert audit is not None
    assert alerts is not None
    assert database is not None
    assert authorization is not None
    assert configuration is not None
    assert entry_gate is not None
    assert clock is not None
    events = PaperEventProjector(accounting=accounting, audit=audit, alerts=alerts)
    runtime = PaperRuntime(submissions=submissions, events=events, alerts=alerts, clock=clock)
    runner = PaperCycleRunner(
        runtime=runtime,
        source=source,
        database=database,
        authorization=authorization,
        configuration=configuration,
        entry_gate=entry_gate,
        clock=clock,
        max_market_age=max_market_age,
    )
    return Phase6Components(runtime=runtime, runner=runner)


__all__ = ["Phase6Components", "Phase6CompositionError", "compose_phase6"]
