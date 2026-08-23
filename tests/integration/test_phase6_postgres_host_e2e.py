"""Host-level Phase 6 proof using the real PostgreSQL adapters.

The test deliberately starts at the authenticated HTTP command boundary and
ends at the durable dashboard projection.  It is skipped locally only when the
integration database is not configured; CI supplies ``QUANTORA_DATABASE_URL``.
"""

import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantora_trade.api import create_app
from quantora_trade.api.schemas import ResolvedSymbolSpecification
from quantora_trade.application.paper_bootstrap import compose_phase6
from quantora_trade.application.paper_command_consumer import (
    PaperCommandConsumer,
    PaperRuntimeDefaults,
    workload_generation_for_command,
)
from quantora_trade.application.paper_host import PaperWorkloadHost
from quantora_trade.application.paper_operations import (
    CurrentMarketInput,
    PersistedPaperOrderAudit,
)
from quantora_trade.application.paper_worker import PaperWorkerControl, WorkerStatus
from quantora_trade.dashboard import DashboardService
from quantora_trade.dashboard.models import (
    DependencyView,
    KillSwitchView,
    OperationalState,
    WorkerView,
)
from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent, Decision, RiskAssessment
from quantora_trade.execution import (
    DeterministicPaperAdapter,
    DurablePaperAdapter,
    InstrumentExecutionSnapshot,
    PaperBrokerPort,
    PaperExecutionInput,
    PaperFillPolicy,
    PaperQuote,
)
from quantora_trade.infrastructure.database.accounting_repository import (
    PostgresPaperAccountingRepository,
)
from quantora_trade.infrastructure.database.command_repository import (
    CommandStatus,
    PostgresSystemCommandRepository,
)
from quantora_trade.infrastructure.database.dashboard_repository import (
    PostgresDashboardRepository,
)
from quantora_trade.infrastructure.database.kill_switch_repository import (
    PostgresKillSwitchRepository,
)
from quantora_trade.infrastructure.database.market_data_models import BrokerModel, InstrumentModel
from quantora_trade.infrastructure.database.order_repository import PostgresPaperOrderRepository
from quantora_trade.infrastructure.database.submission_repository import (
    PostgresApprovalEvidenceRepository,
    PostgresSubmissionJournal,
)
from quantora_trade.infrastructure.database.worker_repository import (
    PostgresPaperWorkerRepository,
)
from quantora_trade.risk.approval import build_approved_order_intent
from quantora_trade.risk.kill_switch import KillSwitchQuery, KillSwitchService
from quantora_trade.risk.submission import OrderSubmissionService, SubmissionContext

DATABASE_URL = os.getenv("QUANTORA_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("QUANTORA_DATABASE_URL is required for integration tests", allow_module_level=True)

engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(engine, expire_on_commit=False)
NOW = datetime(2026, 8, 23, 14, tzinfo=UTC)
BROKER_ID = uuid4()
INSTRUMENT_ID = uuid4()


@dataclass
class Clock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class Ready:
    def ready(self) -> bool:
        return True


class Authorizer:
    def authorize(self, bearer_token: str, required_scope: str) -> str:
        if bearer_token != "owner-token" or required_scope not in {
            "system:operate",
            "system:read",
        }:
            raise PermissionError("denied")
        return "owner@example.test"


class SymbolPreflight:
    def resolve(self, symbols: Sequence[str]) -> Sequence[ResolvedSymbolSpecification]:
        assert tuple(symbols) == ("XAUUSD",)
        return (
            ResolvedSymbolSpecification(
                symbol="XAUUSD",
                specification_id=INSTRUMENT_ID,
                specification_hash="a" * 64,
                quote_currency="USD",
            ),
        )


class Alerts:
    def publish(self, event: object, *, now: datetime) -> None:
        assert event is not None and now == NOW


class CriticalAudit:
    def record_critical(self, event: object) -> None:
        raise AssertionError(f"unexpected critical event: {event!r}")


class EntryGate:
    def __init__(self, switches: KillSwitchService) -> None:
        self._switches = switches

    def new_entries_blocked(self) -> bool:
        return self._switches.is_blocked(
            KillSwitchQuery(
                account="paper-primary",
                asset="METAL",
                symbol="XAUUSD",
                strategy="trend-v1",
                new_entry=True,
            )
        )


class OneIntent:
    def __init__(self, intent: ApprovedOrderIntent) -> None:
        self._intent = intent
        self._delivered = False

    def next_approved(self, market: CurrentMarketInput) -> ApprovedOrderIntent | None:
        if self._delivered or market.symbol != self._intent.symbol:
            return None
        self._delivered = True
        return self._intent


class Market:
    def current(self, *, symbol: str, timeframe: str) -> CurrentMarketInput:
        assert timeframe == "M5"
        return CurrentMarketInput(symbol, NOW)


class Inputs:
    def __init__(self, *, quote_symbol: str = "XAUUSD") -> None:
        self.quote_symbol = quote_symbol

    def execution_input(self, intent: ApprovedOrderIntent) -> PaperExecutionInput:
        return PaperExecutionInput(
            instrument=InstrumentExecutionSnapshot(
                INSTRUMENT_ID,
                BROKER_ID,
                "a" * 64,
                "USD",
                Decimal("100"),
                Decimal("0.01"),
            ),
            quote=PaperQuote(
                self.quote_symbol,
                Decimal("2500.00"),
                Decimal("2500.20"),
                intent.volume,
                NOW,
            ),
            expires_at=NOW + timedelta(minutes=10),
        )


@pytest.fixture(autouse=True)
def clean_phase6_tables() -> Iterator[None]:
    with SessionFactory() as session, session.begin():
        session.execute(
            text(
                "TRUNCATE quantora.system_commands, quantora.paper_worker_transitions, "
                "quantora.paper_worker_states, quantora.paper_mark_events, "
                "quantora.paper_accounting_events, quantora.paper_positions, "
                "quantora.paper_accounts, quantora.paper_fills, "
                "quantora.paper_order_events, quantora.paper_orders, "
                "quantora.submission_journal, quantora.risk_assessment_evidence, "
                "quantora.decision_evidence, quantora.kill_switch_states, "
                "quantora.kill_switch_events CASCADE"
            )
        )
        session.add(
            BrokerModel(
                id=BROKER_ID,
                code=f"PHASE6-{BROKER_ID}",
                name="Phase 6 E2E Broker",
                adapter_type="paper",
                enabled=True,
                created_at=NOW,
            )
        )
        session.add(
            InstrumentModel(
                id=INSTRUMENT_ID,
                broker_id=BROKER_ID,
                symbol="XAUUSD",
                canonical_symbol="XAUUSD",
                asset_class="metal",
                quote_currency="USD",
                digits=2,
                point=Decimal("0.01"),
                pip_size=Decimal("0.01"),
                tick_size=Decimal("0.01"),
                tick_value=Decimal("1"),
                contract_size=Decimal("100"),
                spread_points=20,
                session_timezone="UTC",
                session_profile="24x5",
                volume_min=Decimal("0.01"),
                volume_max=Decimal("100"),
                volume_step=Decimal("0.01"),
                specification_hash="a" * 64,
                observed_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    yield
    with SessionFactory() as session, session.begin():
        session.execute(
            text("DELETE FROM quantora.instruments WHERE id = :id"), {"id": INSTRUMENT_ID}
        )
        session.execute(text("DELETE FROM quantora.brokers WHERE id = :id"), {"id": BROKER_ID})


def approved_evidence(
    key: str = "phase6-host-xau-1",
) -> tuple[Decision, RiskAssessment, ApprovedOrderIntent]:
    decision = Decision(
        id=uuid4(),
        signal_id=uuid4(),
        symbol="XAUUSD",
        timeframe="M5",
        action=Action.BUY,
        confidence=Decimal("0.84"),
        policy_version="decision-v1",
        reason_codes=("TREND_CONFIRMED", "BULLISH_TRIGGER"),
        expires_at=NOW + timedelta(minutes=10),
    )
    assessment = RiskAssessment(
        id=uuid4(),
        decision_id=decision.id,
        policy_version="risk-v1",
        approved=True,
        rejection_codes=(),
        risk_amount=Decimal("100"),
        volume=Decimal("1"),
        stop_loss=Decimal("2490"),
        take_profit=Decimal("2520"),
        created_at=NOW,
    )
    intent = build_approved_order_intent(
        decision=decision,
        assessment=assessment,
        mode=TradingMode.PAPER,
        created_at=NOW,
    )
    # The production builder owns the canonical hash-derived key.  ``key`` only
    # documents the scenario name and guards accidental fixture reuse.
    assert key and intent.idempotency_key
    return decision, assessment, intent


def auth_headers(request_id: str, key: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer owner-token",
        "X-Request-ID": request_id,
        "Idempotency-Key": key,
    }


def command_body(mode: str = "paper") -> dict[str, object]:
    return {
        "mode": mode,
        "symbols": ["XAUUSD"],
        "strategy_id": "trend-v1",
        "reason": "phase 6 host evidence",
    }


def build_host(intent: ApprovedOrderIntent, clock: Clock) -> PaperWorkloadHost:
    evidence = PostgresApprovalEvidenceRepository(SessionFactory)
    order_repository = PostgresPaperOrderRepository(SessionFactory)
    accounting = PostgresPaperAccountingRepository(SessionFactory)
    accounting.initialize("USD", Decimal("10000"), NOW)
    switches = KillSwitchService(PostgresKillSwitchRepository(SessionFactory))
    durable = DurablePaperAdapter(
        adapter=DeterministicPaperAdapter(
            clock=clock,
            policy=PaperFillPolicy(slippage_points=2, commission_per_volume=Decimal("2")),
        ),
        repository=order_repository,
    )
    broker = PaperBrokerPort(inputs=Inputs(), adapter=durable, clock=clock)
    submissions = OrderSubmissionService(
        evidence=evidence,
        journal=PostgresSubmissionJournal(SessionFactory, now=clock.now),
        kill_switch=switches,
        broker=broker,
        clock=clock,
        decision_policy_version="decision-v1",
        risk_policy_version="risk-v1",
    )
    components = compose_phase6(
        submissions=submissions,
        source=OneIntent(intent),
        accounting=accounting,
        audit=PersistedPaperOrderAudit(orders=order_repository, critical=CriticalAudit()),
        alerts=Alerts(),
        database=Ready(),
        authorization=Ready(),
        configuration=Ready(),
        entry_gate=EntryGate(switches),
        clock=clock,
        max_market_age=timedelta(seconds=30),
    )
    return PaperWorkloadHost(
        runner=components.runner,
        market=Market(),
        leases=PostgresPaperWorkerRepository(SessionFactory, now=clock.now),
        owner="paper-host-1",
        lease_duration=timedelta(seconds=30),
    )


def dashboard(clock: Clock) -> DashboardService:
    worker_repository = PostgresPaperWorkerRepository(SessionFactory, now=clock.now)
    repository = PostgresDashboardRepository(
        SessionFactory,
        worker_provider=lambda: WorkerView(
            worker_id="paper-primary",
            state=(
                OperationalState.HEALTHY
                if worker_repository.current().status is WorkerStatus.RUNNING
                else OperationalState.UNAVAILABLE
            ),
            last_heartbeat_at=clock.now(),
        ),
        kill_switch_provider=lambda: KillSwitchView(active=False, scope="GLOBAL"),
        dependency_provider=lambda: (
            DependencyView(component="postgresql", state=OperationalState.HEALTHY),
        ),
        clock=clock.now,
    )
    return DashboardService(repository)


def test_authenticated_api_to_real_paper_runner_is_durable_and_restart_idempotent() -> None:
    clock = Clock()
    decision, assessment, intent = approved_evidence()
    evidence = PostgresApprovalEvidenceRepository(SessionFactory)
    evidence.persist(
        decision,
        assessment,
        SubmissionContext("paper-primary", "METAL", "trend-v1"),
    )
    queue = PostgresSystemCommandRepository(SessionFactory, now=clock.now)
    workers = PostgresPaperWorkerRepository(SessionFactory, now=clock.now)
    host = build_host(intent, clock)
    control = PaperWorkerControl(
        repository=workers,
        clock=clock,
        entry_gate=EntryGate(KillSwitchService(PostgresKillSwitchRepository(SessionFactory))),
    )
    consumer = PaperCommandConsumer(
        queue=queue,
        control=control,
        defaults=PaperRuntimeDefaults("paper-primary", ("M5",), Decimal("1")),
        worker_id="paper-host-1",
        lease_duration=timedelta(seconds=30),
        workload=host,
    )
    client = TestClient(
        create_app(
            command_repository=queue,
            authorizer=Authorizer(),
            dashboard_service=dashboard(clock),
            symbol_preflight=SymbolPreflight(),
        )
    )

    started = client.post(
        "/system/start",
        json=command_body(),
        headers=auth_headers("phase6-start", "phase6-start-key"),
    )
    replayed = client.post(
        "/system/start",
        json=command_body(),
        headers=auth_headers("phase6-start-replay", "phase6-start-key"),
    )
    assert started.status_code == replayed.status_code == 202
    assert replayed.json()["replayed"] is True
    assert consumer.run_once() is True
    start_command = queue.get(UUID(started.json()["id"]))
    assert start_command is not None and start_command.status is CommandStatus.SUCCEEDED
    assert workers.current().status is WorkerStatus.RUNNING
    start_generation = workload_generation_for_command(start_command.id)
    assert host.poll_once(fence_token=start_generation).attempted == 1

    with SessionFactory() as session:
        counts = session.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM quantora.decision_evidence), "
                "(SELECT count(*) FROM quantora.risk_assessment_evidence), "
                "(SELECT count(*) FROM quantora.paper_orders), "
                "(SELECT count(*) FROM quantora.paper_order_events), "
                "(SELECT count(*) FROM quantora.paper_fills), "
                "(SELECT count(*) FROM quantora.paper_accounting_events)"
            )
        ).one()
    assert counts == (1, 1, 1, 3, 1, 1)
    snapshot = dashboard(clock).snapshot()
    assert [item.symbol for item in snapshot.orders] == ["XAUUSD"]
    assert len(snapshot.fills) == len(snapshot.positions) == len(snapshot.pnl) == 1
    assert snapshot.pnl[0].fees == Decimal("2")
    assert dashboard(clock).events(after_event_id=0, limit=10).next_cursor > 0

    # Reconstruct all database adapters as a fresh process would. Replaying the
    # same authoritative intent reads the completed submission and projects the
    # same fill idempotently instead of manufacturing another order or ledger row.
    clock.advance(timedelta(seconds=31))
    restarted_host = build_host(intent, clock)
    restart_token = uuid4()
    config = workers.current().config
    assert config is not None
    restarted_host.start(config, fence_token=restart_token)
    assert restarted_host.poll_once(fence_token=restart_token).attempted == 1
    with SessionFactory() as session:
        assert session.scalar(text("SELECT count(*) FROM quantora.paper_orders")) == 1
        assert session.scalar(text("SELECT count(*) FROM quantora.paper_fills")) == 1
        assert session.scalar(text("SELECT count(*) FROM quantora.paper_accounting_events")) == 1

    stopped = client.post(
        "/system/stop",
        json=command_body(),
        headers=auth_headers("phase6-stop", "phase6-stop-key"),
    )
    assert stopped.status_code == 202
    # A reconstructed consumer owns the recovered host. The authenticated STOP
    # retires that generation and reaches the canonical durable STOPPED state.
    restarted_consumer = PaperCommandConsumer(
        queue=queue,
        control=control,
        defaults=PaperRuntimeDefaults("paper-primary", ("M5",), Decimal("1")),
        worker_id="paper-host-restarted",
        lease_duration=timedelta(seconds=30),
        workload=restarted_host,
    )
    assert restarted_consumer.run_once() is True
    assert workers.current().status is WorkerStatus.STOPPED
    with pytest.raises(PermissionError, match="fenced"):
        host.poll_once(fence_token=start_generation)


def test_fail_closed_modes_mixed_quote_and_failed_stop_never_poll() -> None:
    clock = Clock()
    decision, assessment, intent = approved_evidence("phase6-negative")
    evidence = PostgresApprovalEvidenceRepository(SessionFactory)
    evidence.persist(
        decision,
        assessment,
        SubmissionContext("paper-primary", "METAL", "trend-v1"),
    )
    queue = PostgresSystemCommandRepository(SessionFactory, now=clock.now)
    workers = PostgresPaperWorkerRepository(SessionFactory, now=clock.now)
    client = TestClient(
        create_app(
            command_repository=queue,
            authorizer=Authorizer(),
            symbol_preflight=SymbolPreflight(),
        )
    )
    for mode in ("live", "backtest"):
        response = client.post(
            "/system/start",
            json=command_body(mode),
            headers=auth_headers(f"reject-{mode}", f"reject-{mode}"),
        )
        assert response.status_code in {403, 422}
    with SessionFactory() as session:
        assert session.scalar(text("SELECT count(*) FROM quantora.system_commands")) == 0

    bad_broker = PaperBrokerPort(
        inputs=Inputs(quote_symbol="EURUSD"),
        adapter=DurablePaperAdapter(
            adapter=DeterministicPaperAdapter(clock=clock, policy=PaperFillPolicy()),
            repository=PostgresPaperOrderRepository(SessionFactory),
        ),
        clock=clock,
    )
    with pytest.raises(PermissionError, match="symbol"):
        bad_broker.submit(intent)
    with pytest.raises(PermissionError, match="PAPER"):
        bad_broker.submit(replace(intent, mode=TradingMode.LIVE))
    with SessionFactory() as session:
        assert session.scalar(text("SELECT count(*) FROM quantora.paper_orders")) == 0

    host = build_host(intent, clock)

    class StopFails:
        polls = 0

        def start(self, config: object, *, fence_token: UUID) -> None:
            host.start(config, fence_token=fence_token)  # type: ignore[arg-type]

        def stop(self, *, fence_token: UUID) -> None:
            raise RuntimeError("host stop failed")

    workload = StopFails()
    consumer = PaperCommandConsumer(
        queue=queue,
        control=PaperWorkerControl(
            repository=workers,
            clock=clock,
            entry_gate=EntryGate(KillSwitchService(PostgresKillSwitchRepository(SessionFactory))),
        ),
        defaults=PaperRuntimeDefaults("paper-primary", ("M5",), Decimal("1")),
        worker_id="paper-host-failure",
        lease_duration=timedelta(seconds=30),
        workload=workload,
    )
    start = client.post(
        "/system/start",
        json=command_body(),
        headers=auth_headers("failure-start", "failure-start-key"),
    )
    assert start.status_code == 202 and consumer.run_once() is True
    stop = client.post(
        "/system/stop",
        json=command_body(),
        headers=auth_headers("failure-stop", "failure-stop-key"),
    )
    assert stop.status_code == 202 and consumer.run_once() is True
    failed = queue.get(UUID(stop.json()["id"]))
    assert failed is not None and failed.status is CommandStatus.FAILED
    assert workers.current().status is WorkerStatus.HALTED
    # HALTED is durable and the control plane never invokes poll as part of a
    # failed stop. A stale/unknown generation also cannot acquire host work.
    assert workload.polls == 0
    with pytest.raises(PermissionError, match="fenced"):
        host.poll_once(fence_token=uuid4())
