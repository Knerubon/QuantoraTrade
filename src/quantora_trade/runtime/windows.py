"""Windows PAPER smoke runtime backed by PostgreSQL.

This composition validates the durable API/command/worker plumbing and observes
current persisted candles.  It deliberately has no signal or order source, so it
cannot be used as empirical PAPER trading evidence and has no LIVE path.
"""

import argparse
import hmac
import os
import socket
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from quantora_trade import __version__
from quantora_trade.api import create_app
from quantora_trade.api.schemas import ResolvedSymbolSpecification, ServiceStatus
from quantora_trade.application.paper_command_consumer import (
    PaperCommandConsumer,
    PaperRuntimeDefaults,
)
from quantora_trade.application.paper_host import PaperWorkloadHost
from quantora_trade.application.paper_operations import (
    CurrentMarketInput,
    CycleReport,
    PaperCycleRunner,
)
from quantora_trade.application.paper_worker import PaperWorkerControl, WorkerStatus
from quantora_trade.dashboard.models import (
    DependencyView,
    KillSwitchView,
    OperationalState,
    WorkerView,
)
from quantora_trade.dashboard.service import DashboardService
from quantora_trade.domain.enums import TradingMode
from quantora_trade.infrastructure.database.command_repository import (
    PostgresSystemCommandRepository,
)
from quantora_trade.infrastructure.database.dashboard_repository import (
    PostgresDashboardRepository,
)
from quantora_trade.infrastructure.database.kill_switch_repository import (
    PostgresKillSwitchRepository,
)
from quantora_trade.infrastructure.database.market_data_models import (
    BrokerModel,
    CandleModel,
    InstrumentModel,
)
from quantora_trade.infrastructure.database.worker_repository import (
    PostgresPaperWorkerRepository,
)
from quantora_trade.risk.kill_switch import KillSwitchQuery, KillSwitchService


def _database_url() -> str:
    value = os.environ.get("QUANTORA_DATABASE_URL", "")
    if not value.startswith("postgresql+psycopg://"):
        raise RuntimeError("QUANTORA_DATABASE_URL must use postgresql+psycopg")
    return value


def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(create_engine(_database_url(), pool_pre_ping=True), expire_on_commit=False)


class EnvironmentTokenAuthorizer:
    """Single local operator token with constant-time verification."""

    def __init__(self) -> None:
        self._token = os.environ.get("QUANTORA_API_TOKEN", "")
        if len(self._token) < 24:
            raise RuntimeError("QUANTORA_API_TOKEN must contain at least 24 characters")

    def authorize(self, bearer_token: str, required_scope: str) -> str:
        if not hmac.compare_digest(bearer_token, self._token):
            raise PermissionError("invalid token")
        if required_scope not in {"system:read", "system:operate"}:
            raise PermissionError("unsupported scope")
        return "windows-paper-operator"


class PostgresSymbolPreflight:
    def __init__(self, sessions: sessionmaker[Session], max_age: timedelta) -> None:
        self._sessions = sessions
        self._max_age = max_age

    def resolve(self, symbols: Sequence[str]) -> Sequence[ResolvedSymbolSpecification]:
        now = datetime.now(UTC)
        resolved: list[ResolvedSymbolSpecification] = []
        with self._sessions() as session:
            for symbol in symbols:
                rows = session.scalars(
                    select(InstrumentModel)
                    .join(BrokerModel, BrokerModel.id == InstrumentModel.broker_id)
                    .where(
                        InstrumentModel.canonical_symbol == symbol,
                        BrokerModel.enabled.is_(True),
                    )
                ).all()
                if len(rows) != 1:
                    raise LookupError(f"{symbol} requires exactly one enabled broker specification")
                row = rows[0]
                observed = row.observed_at.astimezone(UTC)
                resolved.append(
                    ResolvedSymbolSpecification(
                        symbol=symbol,
                        specification_id=row.id,
                        specification_hash=row.specification_hash,
                        quote_currency=row.quote_currency,
                        active=True,
                        stale=now - observed > self._max_age,
                    )
                )
        return tuple(resolved)


class PostgresCurrentMarket:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def current(self, *, symbol: str, timeframe: str) -> CurrentMarketInput | None:
        with self._sessions() as session:
            observed = session.scalar(
                select(func.max(CandleModel.close_time))
                .join(InstrumentModel, InstrumentModel.id == CandleModel.instrument_id)
                .where(
                    InstrumentModel.canonical_symbol == symbol,
                    CandleModel.timeframe == timeframe,
                    CandleModel.is_closed.is_(True),
                )
            )
        if observed is None:
            return None
        return CurrentMarketInput(symbol=symbol, observed_at=observed.astimezone(UTC))


class ObservationOnlyRunner(PaperCycleRunner):
    """Bounded smoke runner; it can never manufacture or submit an order."""

    def __init__(self) -> None:
        # The smoke implementation overrides run_cycle completely and therefore
        # intentionally does not construct an execution runtime.
        pass

    def run_cycle(self, market: CurrentMarketInput, *, max_workload: int = 1) -> CycleReport:
        if max_workload != 1:
            raise ValueError("observation-only runtime accepts one unit of work")
        if datetime.now(UTC) - market.observed_at > timedelta(minutes=30):
            raise PermissionError("persisted market observation is stale")
        return CycleReport(attempted=0, results=())


def _database_ready(sessions: sessionmaker[Session]) -> bool:
    try:
        with sessions() as session:
            return bool(session.scalar(text("SELECT 1")) == 1)
    except Exception:
        return False


def build_api() -> FastAPI:
    sessions = _session_factory()
    workers = PostgresPaperWorkerRepository(sessions)
    switches = KillSwitchService(PostgresKillSwitchRepository(sessions))

    def status() -> ServiceStatus:
        database_ready = _database_ready(sessions)
        state = workers.current() if database_ready else None
        active_switch = switches.is_blocked(KillSwitchQuery())
        running = state is not None and state.status is WorkerStatus.RUNNING
        return ServiceStatus(
            version=__version__,
            environment="windows-smoke",
            mode=TradingMode.PAPER,
            ready=database_ready and running and not active_switch,
            database_ready=database_ready,
            broker_connected=False,
            kill_switch_active=active_switch,
            worker_state=state.status.value if state else None,
            data_connected=database_ready,
            code_version=os.environ.get("QUANTORA_CODE_VERSION"),
            degraded_reason_codes=("SMOKE_ONLY_NO_ORDER_EXECUTION",),
        )

    def worker_view() -> WorkerView:
        state = workers.current()
        return WorkerView(
            worker_id="windows-paper-smoke",
            state=OperationalState.HEALTHY
            if state.status is WorkerStatus.RUNNING
            else OperationalState.UNAVAILABLE,
            last_heartbeat_at=state.changed_at,
        )

    dashboard = DashboardService(
        PostgresDashboardRepository(
            sessions,
            worker_provider=worker_view,
            kill_switch_provider=lambda: KillSwitchView(
                active=switches.is_blocked(KillSwitchQuery()), scope="GLOBAL"
            ),
            dependency_provider=lambda: (
                DependencyView(
                    component="postgresql",
                    state=OperationalState.HEALTHY
                    if _database_ready(sessions)
                    else OperationalState.UNAVAILABLE,
                ),
            ),
        )
    )
    return create_app(
        status,
        command_repository=PostgresSystemCommandRepository(sessions),
        authorizer=EnvironmentTokenAuthorizer(),
        dashboard_service=dashboard,
        symbol_preflight=PostgresSymbolPreflight(sessions, timedelta(hours=24)),
    )


def run_worker() -> None:
    if os.environ.get("QUANTORA_TRADING_MODE", "").lower() != "paper":
        raise RuntimeError("QUANTORA_TRADING_MODE=paper is required")
    if os.environ.get("QUANTORA_SMOKE_ONLY", "").lower() != "true":
        raise RuntimeError("QUANTORA_SMOKE_ONLY=true is required")
    sessions = _session_factory()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    switches = KillSwitchService(PostgresKillSwitchRepository(sessions))
    workers = PostgresPaperWorkerRepository(sessions)
    control = PaperWorkerControl(
        repository=workers,
        clock=type("Clock", (), {"now": staticmethod(lambda: datetime.now(UTC))})(),
        entry_gate=type(
            "Gate", (), {"new_entries_blocked": lambda self: switches.is_blocked(KillSwitchQuery())}
        )(),
    )
    host = PaperWorkloadHost(
        runner=ObservationOnlyRunner(),
        market=PostgresCurrentMarket(sessions),
        leases=workers,
        owner=worker_id,
        lease_duration=timedelta(seconds=30),
    )
    consumer = PaperCommandConsumer(
        queue=PostgresSystemCommandRepository(sessions),
        control=control,
        defaults=PaperRuntimeDefaults("paper-smoke", ("M5",), Decimal("5")),
        worker_id=worker_id,
        lease_duration=timedelta(seconds=30),
        workload=host,
    )
    while True:
        consumed = consumer.run_once()
        if host.running and host.active_generation is not None:
            host.poll_once(fence_token=host.active_generation)
        time.sleep(1 if consumed else 5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=("api", "worker"))
    args = parser.parse_args()
    if args.service == "api":
        uvicorn.run(build_api(), host="127.0.0.1", port=8000)
    else:
        run_worker()


if __name__ == "__main__":
    main()
