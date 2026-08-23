"""Explicit, bounded Phase 6 PAPER composition.

Nothing in this module starts a loop or creates a signal.  A host process must
provide a current market observation and invoke :meth:`PaperCycleRunner.run_cycle`.
"""

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from quantora_trade.domain.enums import TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent, RiskAssessment, Signal
from quantora_trade.domain.ports import BrokerOrderResult
from quantora_trade.execution.runtime import (
    AlertPublisher,
    PaperRuntime,
    PaperRuntimeEvent,
    PaperRuntimeEventKind,
)
from quantora_trade.notifications.models import AlertEvent, AlertSeverity


class FillAccountingPort(Protocol):
    def project_fill(
        self,
        order_id: UUID,
        fill_sequence: int,
        *,
        recorded_at: datetime,
    ) -> object: ...


class OperationalAuditPort(Protocol):
    """Durable, idempotent operational event sink."""

    def record(self, event: PaperRuntimeEvent) -> None: ...


class DurableOrderQuery(Protocol):
    def get(self, idempotency_key: str) -> object | None: ...


class CriticalAuditPort(Protocol):
    def record_critical(self, event: PaperRuntimeEvent) -> None: ...


class PersistedPaperOrderAudit:
    """Audit adapter backed by durable orders plus a critical-event journal."""

    def __init__(self, *, orders: DurableOrderQuery, critical: CriticalAuditPort) -> None:
        self._orders = orders
        self._critical = critical

    def record(self, event: PaperRuntimeEvent) -> None:
        if event.order is None:
            if event.kind is not PaperRuntimeEventKind.CRITICAL:
                raise ValueError("only a critical audit event may omit its order")
            self._critical.record_critical(event)
            return
        persisted = self._orders.get(event.idempotency_key)
        if persisted != event.order:
            raise RuntimeError("runtime event does not match durable PAPER order evidence")


class PaperEventProjector:
    """Project runtime events to accounting, durable audit/dashboard, and alerts."""

    def __init__(
        self,
        *,
        accounting: FillAccountingPort,
        audit: OperationalAuditPort,
        alerts: AlertPublisher,
    ) -> None:
        self._accounting = accounting
        self._audit = audit
        self._alerts = alerts

    def emit(self, event: PaperRuntimeEvent) -> None:
        order = event.order
        if event.kind is PaperRuntimeEventKind.FILL:
            if order is None:
                raise ValueError("fill event requires an order snapshot")
            for sequence, _fill in enumerate(order.fills, start=1):
                self._accounting.project_fill(
                    order.id,
                    sequence,
                    recorded_at=event.occurred_at,
                )
        # The sink is mandatory and must persist idempotently. Dashboard queries
        # consume the order/accounting projections rather than raw provider data.
        self._audit.record(event)
        self._notify(event)

    def _notify(self, event: PaperRuntimeEvent) -> None:
        severity = {
            PaperRuntimeEventKind.ACCEPTED: AlertSeverity.INFO,
            PaperRuntimeEventKind.FILL: AlertSeverity.INFO,
            PaperRuntimeEventKind.REJECTED: AlertSeverity.WARNING,
            PaperRuntimeEventKind.CRITICAL: AlertSeverity.CRITICAL,
        }[event.kind]
        alert = AlertEvent(
            event_code=f"PAPER_ORDER_{event.kind.value.upper()}",
            severity=severity,
            component="paper-operations",
            message=f"PAPER order event: {event.kind.value}",
            dedup_key=f"paper-event:{event.idempotency_key}:{event.kind.value}:{event.code}",
            cooldown=timedelta(minutes=1),
            occurred_at=event.occurred_at,
            payload=MappingProxyType(
                {"idempotency_key": event.idempotency_key, "reason_code": event.code}
            ),
        )
        # Provider delivery remains best-effort; accounting/audit above are not.
        with suppress(Exception):
            self._alerts.publish(alert, now=event.occurred_at)


class PaperNotificationHooks:
    """Typed, non-executing notification hooks for upstream signal/risk outcomes."""

    def __init__(self, alerts: AlertPublisher) -> None:
        self._alerts = alerts

    def signal(self, signal: Signal) -> object:
        return self._alerts.publish(
            AlertEvent(
                event_code="PAPER_SIGNAL_OBSERVED",
                severity=AlertSeverity.INFO,
                component="paper-signal",
                message="Strategy produced a typed signal",
                dedup_key=f"paper-signal:{signal.id}",
                cooldown=timedelta(0),
                occurred_at=signal.observed_at,
                payload=MappingProxyType(
                    {
                        "signal_id": str(signal.id),
                        "symbol": signal.symbol,
                        "action": signal.action.value,
                        "reason_codes": signal.reason_codes,
                    }
                ),
            ),
            now=signal.observed_at,
        )

    def risk_rejection(self, assessment: RiskAssessment) -> object:
        if assessment.approved:
            raise ValueError("risk_rejection hook requires a rejected assessment")
        return self._alerts.publish(
            AlertEvent(
                event_code="PAPER_RISK_REJECTED",
                severity=AlertSeverity.WARNING,
                component="paper-risk",
                message="Risk engine rejected a PAPER candidate",
                dedup_key=f"paper-risk-rejected:{assessment.id}",
                cooldown=timedelta(0),
                occurred_at=assessment.created_at,
                payload=MappingProxyType(
                    {
                        "assessment_id": str(assessment.id),
                        "reason_codes": assessment.rejection_codes,
                    }
                ),
            ),
            now=assessment.created_at,
        )


@dataclass(frozen=True, slots=True)
class CurrentMarketInput:
    symbol: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.strip().upper():
            raise ValueError("market symbol must be canonical uppercase")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != UTC.utcoffset(
            self.observed_at
        ):
            raise ValueError("market observation must be timezone-aware UTC")


class ApprovedIntentSource(Protocol):
    def next_approved(self, market: CurrentMarketInput) -> ApprovedOrderIntent | None: ...


class ReadinessDependency(Protocol):
    def ready(self) -> bool: ...


class NewEntryGate(Protocol):
    def new_entries_blocked(self) -> bool: ...


class CycleClock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class CycleReport:
    attempted: int
    results: tuple[BrokerOrderResult, ...]


class PaperCycleRunner:
    """Run a caller-bounded workload from an existing approved-intent source."""

    def __init__(
        self,
        *,
        runtime: PaperRuntime,
        source: ApprovedIntentSource,
        database: ReadinessDependency,
        authorization: ReadinessDependency,
        configuration: ReadinessDependency,
        entry_gate: NewEntryGate,
        clock: CycleClock,
        max_market_age: timedelta,
    ) -> None:
        if max_market_age <= timedelta(0):
            raise ValueError("max_market_age must be positive")
        self._runtime = runtime
        self._source = source
        self._dependencies = (database, authorization, configuration)
        self._entry_gate = entry_gate
        self._clock = clock
        self._max_market_age = max_market_age

    def run_cycle(self, market: CurrentMarketInput, *, max_workload: int = 1) -> CycleReport:
        if not 1 <= max_workload <= 100:
            raise ValueError("max_workload must be between 1 and 100")
        self._assert_ready(market)
        results: list[BrokerOrderResult] = []
        for _ in range(max_workload):
            # Safety is evaluated for every unit of work, not merely once per poll.
            # A kill switch or dependency can change while a bounded cycle runs.
            self._assert_ready(market)
            intent = self._source.next_approved(market)
            if intent is None:
                break
            if intent.mode is not TradingMode.PAPER or intent.symbol != market.symbol:
                raise PermissionError("approved intent is outside the PAPER market workload")
            self._assert_ready(market)
            results.append(self._runtime.run_once(intent))
        return CycleReport(attempted=len(results), results=tuple(results))

    def _assert_ready(self, market: CurrentMarketInput) -> None:
        try:
            ready = all(dependency.ready() for dependency in self._dependencies)
            blocked = self._entry_gate.new_entries_blocked()
            now = self._clock.now()
        except Exception as error:
            raise PermissionError("PAPER dependencies are unavailable") from error
        if not ready or blocked:
            raise PermissionError("PAPER dependencies are not ready or entries are blocked")
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("cycle clock must return timezone-aware UTC")
        age = now - market.observed_at
        if age < timedelta(0) or age > self._max_market_age:
            raise PermissionError("market input is not current")


__all__ = [
    "ApprovedIntentSource",
    "CriticalAuditPort",
    "CurrentMarketInput",
    "CycleReport",
    "FillAccountingPort",
    "OperationalAuditPort",
    "PaperCycleRunner",
    "PaperEventProjector",
    "PaperNotificationHooks",
    "PersistedPaperOrderAudit",
    "ReadinessDependency",
]
