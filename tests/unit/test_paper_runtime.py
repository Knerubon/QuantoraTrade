"""PAPER runtime remains bounded, explicit, and observable."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent
from quantora_trade.execution import (
    Fill,
    InstrumentExecutionSnapshot,
    OrderEvent,
    OrderStatus,
    PaperBrokerOrderResult,
    PaperOrder,
    PaperOrderRequest,
    PaperRuntime,
    PaperRuntimeEvent,
    PaperRuntimeEventKind,
    PaperRuntimeProjectionError,
)

NOW = datetime(2026, 8, 23, 10, tzinfo=UTC)


@dataclass
class Clock:
    def now(self) -> datetime:
        return NOW


class Events:
    def __init__(self) -> None:
        self.items: list[PaperRuntimeEvent] = []

    def emit(self, event: PaperRuntimeEvent) -> None:
        self.items.append(event)


class Alerts:
    def __init__(self) -> None:
        self.items: list[object] = []

    def publish(self, event: object, *, now: datetime) -> object:
        self.items.append(event)
        return None


class Submissions:
    def __init__(self, result: PaperBrokerOrderResult | Exception) -> None:
        self.result = result
        self.calls = 0

    def submit(self, intent: ApprovedOrderIntent) -> PaperBrokerOrderResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def intent(mode: TradingMode = TradingMode.PAPER) -> ApprovedOrderIntent:
    return ApprovedOrderIntent(
        id=uuid4(),
        risk_assessment_id=uuid4(),
        idempotency_key="approved-paper-1",
        mode=mode,
        symbol="XAUUSD",
        side=Action.BUY,
        volume=Decimal("1"),
        stop_loss=Decimal("2490"),
        take_profit=Decimal("2520"),
        created_at=NOW,
    )


def filled_result(approved: ApprovedOrderIntent) -> PaperBrokerOrderResult:
    request = PaperOrderRequest(
        approved.id,
        approved.idempotency_key,
        TradingMode.PAPER,
        approved.symbol,
        approved.side,
        approved.volume,
        InstrumentExecutionSnapshot(
            uuid4(), uuid4(), "a" * 64, "USD", Decimal("100"), Decimal("0.01")
        ),
        NOW + timedelta(minutes=5),
    )
    order = PaperOrder(
        uuid4(),
        "a" * 64,
        request,
        OrderStatus.FILLED,
        Decimal("1"),
        (Fill(Decimal("1"), Decimal("2500.2"), Decimal("0"), NOW),),
        (
            OrderEvent(1, OrderStatus.CREATED, NOW, "ORDER_CREATED"),
            OrderEvent(2, OrderStatus.ACCEPTED, NOW, "ORDER_ACCEPTED"),
            OrderEvent(3, OrderStatus.FILLED, NOW, "ORDER_FILLED"),
        ),
    )
    return PaperBrokerOrderResult(order)


def test_one_cycle_emits_typed_accepted_and_fill_events() -> None:
    approved = intent()
    submissions = Submissions(filled_result(approved))
    events, alerts = Events(), Alerts()
    runtime = PaperRuntime(
        submissions=submissions,
        events=events,
        alerts=alerts,
        clock=Clock(),  # type: ignore[arg-type]
    )
    result = runtime.run_once(approved)
    assert result.order.status is OrderStatus.FILLED
    assert submissions.calls == 1
    assert [item.kind for item in events.items] == [
        PaperRuntimeEventKind.ACCEPTED,
        PaperRuntimeEventKind.FILL,
    ]
    assert not alerts.items


def test_failure_emits_critical_event_and_best_effort_alert_then_raises() -> None:
    events, alerts = Events(), Alerts()
    runtime = PaperRuntime(
        submissions=Submissions(RuntimeError("uncertain")),  # type: ignore[arg-type]
        events=events,
        alerts=alerts,  # type: ignore[arg-type]
        clock=Clock(),
    )
    with pytest.raises(RuntimeError, match="uncertain"):
        runtime.run_once(intent())
    assert events.items[0].kind is PaperRuntimeEventKind.CRITICAL
    assert len(alerts.items) == 1


def test_live_and_non_intent_are_rejected_before_submission() -> None:
    approved = intent()
    submissions = Submissions(filled_result(approved))
    runtime = PaperRuntime(
        submissions=submissions,  # type: ignore[arg-type]
        events=Events(),
        alerts=Alerts(),  # type: ignore[arg-type]
        clock=Clock(),
    )
    with pytest.raises(PermissionError, match="PAPER"):
        runtime.run_once(intent(TradingMode.LIVE))
    with pytest.raises(TypeError, match="ApprovedOrderIntent"):
        runtime.run_once(object())  # type: ignore[arg-type]
    assert submissions.calls == 0


def test_mandatory_projection_failure_requires_reconciliation() -> None:
    approved = intent()

    class FailedEvents:
        def emit(self, event: PaperRuntimeEvent) -> None:
            raise RuntimeError("audit database unavailable")

    runtime = PaperRuntime(
        submissions=Submissions(filled_result(approved)),
        events=FailedEvents(),
        alerts=Alerts(),  # type: ignore[arg-type]
        clock=Clock(),
    )
    with pytest.raises(PaperRuntimeProjectionError, match="reconciliation"):
        runtime.run_once(approved)


def test_runtime_rejects_naive_clock_before_submission() -> None:
    approved = intent()
    submissions = Submissions(filled_result(approved))

    class NaiveClock:
        def now(self) -> datetime:
            return NOW.replace(tzinfo=None)

    runtime = PaperRuntime(
        submissions=submissions,  # type: ignore[arg-type]
        events=Events(),
        alerts=Alerts(),
        clock=NaiveClock(),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="runtime clock"):
        runtime.run_once(approved)
    assert submissions.calls == 0
