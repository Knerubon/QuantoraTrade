"""Deployable PAPER composition stays bounded and fail-closed."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from quantora_trade.application.paper_operations import (
    CurrentMarketInput,
    PaperCycleRunner,
    PaperEventProjector,
    PaperNotificationHooks,
    PersistedPaperOrderAudit,
)
from quantora_trade.domain.enums import Action, RiskRejectionCode, SignalReasonCode, TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent, RiskAssessment, Signal
from quantora_trade.execution import (
    Fill,
    InstrumentExecutionSnapshot,
    OrderEvent,
    OrderStatus,
    PaperOrder,
    PaperOrderRequest,
    PaperRuntimeEvent,
    PaperRuntimeEventKind,
)

NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class Accounting:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int, str, Decimal]] = []

    def project_fill(
        self,
        order_id: UUID,
        fill_sequence: int,
        *,
        recorded_at: datetime,
    ) -> None:
        assert recorded_at == NOW
        self.calls.append((order_id, fill_sequence, "USD", Decimal("100")))


class Audit:
    def __init__(self, error: Exception | None = None) -> None:
        self.items: list[PaperRuntimeEvent] = []
        self.error = error

    def record(self, event: PaperRuntimeEvent) -> None:
        if self.error:
            raise self.error
        self.items.append(event)


class Alerts:
    def __init__(self, error: Exception | None = None) -> None:
        self.items: list[object] = []
        self.error = error

    def publish(self, event: object, *, now: datetime) -> None:
        if self.error:
            raise self.error
        self.items.append(event)


def filled_order() -> PaperOrder:
    request = PaperOrderRequest(
        uuid4(),
        "paper-composition-1",
        TradingMode.PAPER,
        "XAUUSD",
        Action.BUY,
        Decimal("1"),
        InstrumentExecutionSnapshot(
            uuid4(), uuid4(), "a" * 64, "USD", Decimal("100"), Decimal("0.01")
        ),
        NOW + timedelta(minutes=5),
    )
    return PaperOrder(
        uuid4(),
        "a" * 64,
        request,
        OrderStatus.FILLED,
        Decimal("1"),
        (
            Fill(Decimal("0.4"), Decimal("2500"), Decimal("1"), NOW),
            Fill(Decimal("0.6"), Decimal("2501"), Decimal("1"), NOW),
        ),
        (
            OrderEvent(1, OrderStatus.CREATED, NOW, "ORDER_CREATED"),
            OrderEvent(2, OrderStatus.ACCEPTED, NOW, "ORDER_ACCEPTED"),
            OrderEvent(3, OrderStatus.PARTIAL, NOW, "ORDER_PARTIAL"),
            OrderEvent(4, OrderStatus.FILLED, NOW, "ORDER_FILLED"),
        ),
    )


def test_fill_projects_every_immutable_fill_then_audits_and_notifies() -> None:
    accounting, audit, alerts = Accounting(), Audit(), Alerts()
    projector = PaperEventProjector(
        accounting=accounting,
        audit=audit,
        alerts=alerts,
    )
    order = filled_order()
    event = PaperRuntimeEvent(
        PaperRuntimeEventKind.FILL,
        order.request.idempotency_key,
        NOW,
        order,
        "ORDER_FILLED",
    )

    projector.emit(event)

    assert [item[1] for item in accounting.calls] == [1, 2]
    assert audit.items == [event]
    assert len(alerts.items) == 1


def test_audit_is_mandatory_but_notification_transport_is_best_effort() -> None:
    order = filled_order()
    event = PaperRuntimeEvent(
        PaperRuntimeEventKind.ACCEPTED,
        order.request.idempotency_key,
        NOW,
        order,
        "ORDER_ACCEPTED",
    )
    PaperEventProjector(
        accounting=Accounting(),
        audit=Audit(),
        alerts=Alerts(RuntimeError("telegram unavailable")),
    ).emit(event)
    with pytest.raises(RuntimeError, match="database unavailable"):
        PaperEventProjector(
            accounting=Accounting(),
            audit=Audit(RuntimeError("database unavailable")),
            alerts=Alerts(),
        ).emit(event)


def test_persisted_order_audit_verifies_durable_evidence() -> None:
    order = filled_order()

    class Orders:
        def get(self, idempotency_key: str) -> object | None:
            assert idempotency_key == order.request.idempotency_key
            return order

    class Critical:
        def record_critical(self, event: PaperRuntimeEvent) -> None:
            raise AssertionError("not a critical event")

    PersistedPaperOrderAudit(orders=Orders(), critical=Critical()).record(
        PaperRuntimeEvent(
            PaperRuntimeEventKind.FILL,
            order.request.idempotency_key,
            NOW,
            order,
            "ORDER_FILLED",
        )
    )


def test_persisted_audit_fails_closed_on_missing_or_mismatched_evidence() -> None:
    order = filled_order()

    class MissingOrders:
        def get(self, idempotency_key: str) -> None:
            return None

    class Critical:
        def __init__(self) -> None:
            self.items: list[PaperRuntimeEvent] = []

        def record_critical(self, event: PaperRuntimeEvent) -> None:
            self.items.append(event)

    critical = Critical()
    audit = PersistedPaperOrderAudit(orders=MissingOrders(), critical=critical)
    with pytest.raises(RuntimeError, match="durable PAPER order evidence"):
        audit.record(PaperRuntimeEvent(PaperRuntimeEventKind.FILL, "key", NOW, order, "FILL"))
    with pytest.raises(ValueError, match="critical audit event"):
        audit.record(PaperRuntimeEvent(PaperRuntimeEventKind.ACCEPTED, "key", NOW, None, "BAD"))

    event = PaperRuntimeEvent(PaperRuntimeEventKind.CRITICAL, "key", NOW, None, "FAILED")
    audit.record(event)
    assert critical.items == [event]


def test_fill_event_without_snapshot_fails_before_audit() -> None:
    audit = Audit()
    projector = PaperEventProjector(accounting=Accounting(), audit=audit, alerts=Alerts())
    with pytest.raises(ValueError, match="requires an order"):
        projector.emit(PaperRuntimeEvent(PaperRuntimeEventKind.FILL, "key", NOW, None, "FILL"))
    assert audit.items == []


def test_notification_hooks_publish_typed_signal_and_rejection() -> None:
    alerts = Alerts()
    hooks = PaperNotificationHooks(alerts)  # type: ignore[arg-type]
    signal = Signal(
        uuid4(),
        "XAUUSD",
        "M15",
        Action.BUY,
        Decimal("0.8"),
        "v1",
        (SignalReasonCode.BULLISH_TRIGGER.value,),
        NOW,
        NOW + timedelta(minutes=5),
    )
    rejected = RiskAssessment(
        uuid4(),
        uuid4(),
        "risk-v1",
        False,
        (RiskRejectionCode.DAILY_LOSS_LIMIT.value,),
        Decimal("0"),
        Decimal("0"),
        None,
        None,
        NOW,
    )

    hooks.signal(signal)
    hooks.risk_rejection(rejected)

    assert [item.event_code for item in alerts.items] == [
        "PAPER_SIGNAL_OBSERVED",
        "PAPER_RISK_REJECTED",
    ]
    approved = RiskAssessment(
        uuid4(),
        uuid4(),
        "risk-v1",
        True,
        (),
        Decimal("10"),
        Decimal("0.1"),
        Decimal("2490"),
        None,
        NOW,
    )
    with pytest.raises(ValueError, match="rejected assessment"):
        hooks.risk_rejection(approved)


@dataclass
class Ready:
    value: bool = True

    def ready(self) -> bool:
        return self.value


@dataclass
class Gate:
    blocked: bool = False

    def new_entries_blocked(self) -> bool:
        return self.blocked


class Clock:
    def now(self) -> datetime:
        return NOW


class Source:
    def __init__(self, values: list[ApprovedOrderIntent]) -> None:
        self.values = values

    def next_approved(self, market: CurrentMarketInput) -> ApprovedOrderIntent | None:
        return self.values.pop(0) if self.values else None


class Runtime:
    def __init__(self) -> None:
        self.items: list[ApprovedOrderIntent] = []

    def run_once(self, intent: ApprovedOrderIntent) -> object:
        self.items.append(intent)
        return object()


def intent() -> ApprovedOrderIntent:
    return ApprovedOrderIntent(
        uuid4(),
        uuid4(),
        "approved-composition-1",
        TradingMode.PAPER,
        "XAUUSD",
        Action.BUY,
        Decimal("1"),
        Decimal("2490"),
        Decimal("2520"),
        NOW,
    )


def runner(
    *, database: Ready | None = None, gate: Gate | None = None
) -> tuple[PaperCycleRunner, Runtime]:
    runtime = Runtime()
    database = Ready() if database is None else database
    gate = Gate() if gate is None else gate
    return (
        PaperCycleRunner(
            runtime=runtime,  # type: ignore[arg-type]
            source=Source([intent(), intent()]),
            database=database,
            authorization=Ready(),
            configuration=Ready(),
            entry_gate=gate,
            clock=Clock(),
            max_market_age=timedelta(seconds=30),
        ),
        runtime,
    )


def test_cycle_is_explicit_and_bounded_to_approved_intents() -> None:
    cycle, runtime = runner()
    report = cycle.run_cycle(CurrentMarketInput("XAUUSD", NOW), max_workload=1)
    assert report.attempted == 1
    assert len(runtime.items) == 1


@pytest.mark.parametrize("database,gate", [(Ready(False), Gate()), (Ready(), Gate(True))])
def test_cycle_fails_closed_before_reading_work(database: Ready, gate: Gate) -> None:
    cycle, runtime = runner(database=database, gate=gate)
    with pytest.raises(PermissionError, match=r"not ready|blocked"):
        cycle.run_cycle(CurrentMarketInput("XAUUSD", NOW))
    assert runtime.items == []


def test_cycle_rechecks_kill_gate_after_intent_and_before_submission() -> None:
    gate = Gate()

    class BlockingSource:
        def next_approved(self, market: CurrentMarketInput) -> ApprovedOrderIntent:
            gate.blocked = True
            return intent()

    runtime = Runtime()
    cycle = PaperCycleRunner(
        runtime=runtime,
        source=BlockingSource(),
        database=Ready(),
        authorization=Ready(),
        configuration=Ready(),
        entry_gate=gate,
        clock=Clock(),
        max_market_age=timedelta(seconds=30),
    )

    with pytest.raises(PermissionError, match="blocked"):
        cycle.run_cycle(CurrentMarketInput("XAUUSD", NOW))
    assert runtime.items == []


def test_cycle_rejects_stale_market_input() -> None:
    cycle, runtime = runner()
    with pytest.raises(PermissionError, match="not current"):
        cycle.run_cycle(CurrentMarketInput("XAUUSD", NOW - timedelta(minutes=1)))
    assert runtime.items == []


@pytest.mark.parametrize(
    "symbol,observed_at,message",
    [("xauusd", NOW, "canonical uppercase"), ("XAUUSD", NOW.replace(tzinfo=None), "UTC")],
)
def test_market_input_rejects_noncanonical_or_naive_values(
    symbol: str, observed_at: datetime, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CurrentMarketInput(symbol, observed_at)


def test_cycle_rejects_invalid_bounds_dependency_errors_and_clock_values() -> None:
    cycle, _ = runner()
    market = CurrentMarketInput("XAUUSD", NOW)
    for workload in (0, 101):
        with pytest.raises(ValueError, match="between 1 and 100"):
            cycle.run_cycle(market, max_workload=workload)

    class BrokenReady:
        def ready(self) -> bool:
            raise RuntimeError("database lost")

    broken, _ = runner(database=BrokenReady())  # type: ignore[arg-type]
    with pytest.raises(PermissionError, match="dependencies are unavailable"):
        broken.run_cycle(market)

    class NaiveClock:
        def now(self) -> datetime:
            return NOW.replace(tzinfo=None)

    runtime = Runtime()
    naive = PaperCycleRunner(
        runtime=runtime,
        source=Source([]),
        database=Ready(),
        authorization=Ready(),
        configuration=Ready(),
        entry_gate=Gate(),
        clock=NaiveClock(),
        max_market_age=timedelta(seconds=30),
    )
    with pytest.raises(ValueError, match="cycle clock"):
        naive.run_cycle(market)


def test_cycle_rejects_future_market_and_out_of_scope_intent() -> None:
    cycle, _ = runner()
    with pytest.raises(PermissionError, match="not current"):
        cycle.run_cycle(CurrentMarketInput("XAUUSD", NOW + timedelta(seconds=1)))

    wrong = intent()
    object.__setattr__(wrong, "symbol", "EURUSD")
    runtime = Runtime()
    scoped = PaperCycleRunner(
        runtime=runtime,
        source=Source([wrong]),
        database=Ready(),
        authorization=Ready(),
        configuration=Ready(),
        entry_gate=Gate(),
        clock=Clock(),
        max_market_age=timedelta(seconds=30),
    )
    with pytest.raises(PermissionError, match="outside the PAPER market workload"):
        scoped.run_cycle(CurrentMarketInput("XAUUSD", NOW))
    assert runtime.items == []


def test_runner_rejects_nonpositive_market_age_at_construction() -> None:
    with pytest.raises(ValueError, match="max_market_age must be positive"):
        PaperCycleRunner(
            runtime=Runtime(),
            source=Source([]),
            database=Ready(),
            authorization=Ready(),
            configuration=Ready(),
            entry_gate=Gate(),
            clock=Clock(),
            max_market_age=timedelta(0),
        )
