from datetime import UTC, datetime, timedelta

import pytest

from quantora_trade.domain.enums import TradingMode
from quantora_trade.monitoring import (
    ComponentState,
    HealthReport,
    Heartbeat,
    Readiness,
    ReadinessReason,
    ReconciliationState,
    ReconciliationStatus,
    assess_readiness,
)

NOW = datetime(2026, 8, 23, 3, tzinfo=UTC)


def heartbeat(
    service: str,
    *,
    state: ComponentState = ComponentState.HEALTHY,
    seen_at: datetime = NOW,
) -> Heartbeat:
    return Heartbeat(service, "worker-1", TradingMode.PAPER, state, seen_at, {"region": "local"})


def reconciliation(
    component: str = "paper_broker",
    *,
    state: ReconciliationState = ReconciliationState.MATCHED,
    at: datetime | None = NOW,
    reason_code: str | None = None,
) -> ReconciliationStatus:
    return ReconciliationStatus(component, state, at, reason_code)


def assess(
    *,
    heartbeats: tuple[Heartbeat, ...],
    reconciliations: tuple[ReconciliationStatus, ...] = (),
    required: frozenset[str] = frozenset({"market_data", "paper_execution"}),
) -> HealthReport:
    return assess_readiness(
        assessed_at=NOW,
        required_services=required,
        heartbeats=heartbeats,
        reconciliations=reconciliations,
        heartbeat_max_age=timedelta(seconds=30),
        reconciliation_max_age=timedelta(minutes=1),
    )


def test_ready_when_all_required_dependencies_are_fresh_and_reconciled() -> None:
    report = assess(
        heartbeats=(heartbeat("market_data"), heartbeat("paper_execution")),
        reconciliations=(reconciliation(),),
    )
    assert report.readiness is Readiness.READY
    assert report.reasons == ()


def test_degraded_component_is_non_blocking_but_visible() -> None:
    report = assess(
        heartbeats=(
            heartbeat("market_data", state=ComponentState.DEGRADED),
            heartbeat("paper_execution"),
        )
    )
    assert report.readiness is Readiness.DEGRADED
    assert report.reasons == (ReadinessReason("COMPONENT_DEGRADED", "market_data"),)


def test_blocking_reasons_are_stable_unique_and_sorted() -> None:
    report = assess(
        heartbeats=(
            heartbeat("market_data", seen_at=NOW - timedelta(minutes=2)),
            heartbeat("unused"),
        ),
        reconciliations=(
            reconciliation(
                "positions",
                state=ReconciliationState.MISMATCHED,
                at=None,
                reason_code="POSITION_MISMATCH",
            ),
        ),
    )
    assert report.readiness is Readiness.NOT_READY
    assert report.reasons == (
        ReadinessReason("HEARTBEAT_MISSING", "paper_execution"),
        ReadinessReason("HEARTBEAT_STALE", "market_data"),
        ReadinessReason("POSITION_MISMATCH", "positions"),
    )


def test_duplicate_dependency_reports_fail_closed() -> None:
    report = assess(
        heartbeats=(heartbeat("market_data"), heartbeat("market_data")),
        reconciliations=(reconciliation("positions"), reconciliation("positions")),
        required=frozenset({"market_data"}),
    )
    assert report.readiness is Readiness.NOT_READY
    assert ReadinessReason("HEARTBEAT_DUPLICATE", "market_data") in report.reasons
    assert ReadinessReason("RECONCILIATION_DUPLICATE", "positions") in report.reasons


@pytest.mark.parametrize(
    ("heartbeats", "reconciliations", "expected"),
    [
        (
            (heartbeat("market_data", seen_at=NOW + timedelta(seconds=1)),),
            (),
            "HEARTBEAT_FROM_FUTURE",
        ),
        (
            (heartbeat("market_data", state=ComponentState.UNAVAILABLE),),
            (),
            "COMPONENT_UNAVAILABLE",
        ),
        (
            (heartbeat("market_data"),),
            (reconciliation(state=ReconciliationState.PENDING, at=None),),
            "RECONCILIATION_PENDING",
        ),
        (
            (heartbeat("market_data"),),
            (reconciliation(at=NOW - timedelta(minutes=2)),),
            "RECONCILIATION_STALE",
        ),
        (
            (heartbeat("market_data"),),
            (reconciliation(at=NOW + timedelta(seconds=1)),),
            "RECONCILIATION_FROM_FUTURE",
        ),
    ],
)
def test_unsafe_dependency_states_fail_closed(
    heartbeats: tuple[Heartbeat, ...],
    reconciliations: tuple[ReconciliationStatus, ...],
    expected: str,
) -> None:
    report = assess(
        heartbeats=heartbeats, reconciliations=reconciliations, required=frozenset({"market_data"})
    )
    assert report.readiness is Readiness.NOT_READY
    assert expected in {reason.code for reason in report.reasons}


def test_models_reject_invalid_time_and_inconsistent_ready_state() -> None:
    with pytest.raises(ValueError, match="UTC"):
        heartbeat("market_data", seen_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="requires reconciled_at"):
        reconciliation(at=None)
    with pytest.raises(ValueError, match="cannot contain reasons"):
        HealthReport(Readiness.READY, NOW, (ReadinessReason("BAD", "data"),))
    with pytest.raises(ValueError, match="freshness"):
        assess_readiness(
            assessed_at=NOW,
            required_services=frozenset(),
            heartbeats=(),
            reconciliations=(),
            heartbeat_max_age=timedelta(0),
            reconciliation_max_age=timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="assessed_at"):
        assess_readiness(
            assessed_at=datetime(2026, 1, 1),
            required_services=frozenset(),
            heartbeats=(),
            reconciliations=(),
            heartbeat_max_age=timedelta(seconds=1),
            reconciliation_max_age=timedelta(seconds=1),
        )


def test_heartbeat_details_are_immutable_copies() -> None:
    source = {"state": "ok"}
    value = Heartbeat("data", "one", TradingMode.PAPER, ComponentState.HEALTHY, NOW, source)
    source["state"] = "changed"
    assert value.details["state"] == "ok"
    with pytest.raises(TypeError):
        value.details["state"] = "changed"  # type: ignore[index]
