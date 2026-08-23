"""Pure readiness assessment with stable reason codes."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from quantora_trade.monitoring.models import (
    ComponentState,
    HealthReport,
    Heartbeat,
    Readiness,
    ReadinessReason,
    ReconciliationState,
    ReconciliationStatus,
)


def assess_readiness(
    *,
    assessed_at: datetime,
    required_services: frozenset[str],
    heartbeats: Iterable[Heartbeat],
    reconciliations: Iterable[ReconciliationStatus],
    heartbeat_max_age: timedelta,
    reconciliation_max_age: timedelta,
) -> HealthReport:
    """Assess dependencies without mutating or enabling trading state."""
    if assessed_at.tzinfo is None or assessed_at.utcoffset() != UTC.utcoffset(assessed_at):
        raise ValueError("assessed_at must be timezone-aware UTC")
    if heartbeat_max_age <= timedelta(0) or reconciliation_max_age <= timedelta(0):
        raise ValueError("freshness limits must be greater than zero")

    by_service: dict[str, Heartbeat] = {}
    reasons: set[ReadinessReason] = set()
    blocking = False

    for heartbeat in heartbeats:
        if heartbeat.service_name in by_service:
            reasons.add(ReadinessReason("HEARTBEAT_DUPLICATE", heartbeat.service_name))
            blocking = True
        else:
            by_service[heartbeat.service_name] = heartbeat

    for service in sorted(required_services):
        current = by_service.get(service)
        if current is None:
            reasons.add(ReadinessReason("HEARTBEAT_MISSING", service))
            blocking = True
        elif current.last_seen_at > assessed_at:
            reasons.add(ReadinessReason("HEARTBEAT_FROM_FUTURE", service))
            blocking = True
        elif assessed_at - current.last_seen_at > heartbeat_max_age:
            reasons.add(ReadinessReason("HEARTBEAT_STALE", service))
            blocking = True
        elif current.state is ComponentState.UNAVAILABLE:
            reasons.add(ReadinessReason("COMPONENT_UNAVAILABLE", service))
            blocking = True
        elif current.state is ComponentState.DEGRADED:
            reasons.add(ReadinessReason("COMPONENT_DEGRADED", service))

    reconciliations_by_component: dict[str, ReconciliationStatus] = {}
    for status in reconciliations:
        if status.component in reconciliations_by_component:
            reasons.add(ReadinessReason("RECONCILIATION_DUPLICATE", status.component))
            blocking = True
        else:
            reconciliations_by_component[status.component] = status

    for status in sorted(reconciliations_by_component.values(), key=lambda item: item.component):
        if status.state in {ReconciliationState.MISMATCHED, ReconciliationState.UNKNOWN}:
            reasons.add(
                ReadinessReason(status.reason_code or "RECONCILIATION_UNSAFE", status.component)
            )
            blocking = True
        elif status.state is ReconciliationState.PENDING:
            reasons.add(
                ReadinessReason(status.reason_code or "RECONCILIATION_PENDING", status.component)
            )
            blocking = True
        elif status.reconciled_at is not None and status.reconciled_at > assessed_at:
            reasons.add(ReadinessReason("RECONCILIATION_FROM_FUTURE", status.component))
            blocking = True
        elif status.reconciled_at is not None:
            if assessed_at - status.reconciled_at > reconciliation_max_age:
                reasons.add(ReadinessReason("RECONCILIATION_STALE", status.component))
                blocking = True

    ordered = tuple(sorted(reasons))
    readiness = (
        Readiness.NOT_READY if blocking else Readiness.DEGRADED if ordered else Readiness.READY
    )
    return HealthReport(readiness=readiness, assessed_at=assessed_at, reasons=ordered)
