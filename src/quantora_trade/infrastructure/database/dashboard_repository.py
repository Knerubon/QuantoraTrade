"""Sanitized PostgreSQL read model for PAPER operations."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantora_trade.dashboard.models import (
    DashboardEvent,
    DashboardEventKind,
    DashboardSnapshot,
    DependencyView,
    ExposureView,
    FillView,
    KillSwitchView,
    OperationalState,
    OrderView,
    PaperReport,
    PnlView,
    PositionView,
    TradeView,
    WorkerView,
)
from quantora_trade.domain.enums import TradingMode
from quantora_trade.infrastructure.database.accounting_models import (
    PaperAccountingEventModel,
    PaperAccountModel,
    PaperPositionModel,
)
from quantora_trade.infrastructure.database.order_models import (
    PaperFillModel,
    PaperOrderEventModel,
    PaperOrderModel,
)


def _unavailable_worker() -> WorkerView:
    return WorkerView(
        worker_id="paper", state=OperationalState.UNAVAILABLE, last_heartbeat_at=datetime.now(UTC)
    )


def _safe_kill_switch() -> KillSwitchView:
    return KillSwitchView(active=True, scope="GLOBAL", reason_code="STATUS_UNAVAILABLE")


class PostgresDashboardRepository:
    """Compose read-only DTOs; secrets and raw broker payloads never cross this boundary."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        worker_provider: Callable[[], WorkerView] = _unavailable_worker,
        kill_switch_provider: Callable[[], KillSwitchView] = _safe_kill_switch,
        dependency_provider: Callable[[], tuple[DependencyView, ...]] = tuple,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._worker_provider = worker_provider
        self._kill_switch_provider = kill_switch_provider
        self._dependency_provider = dependency_provider
        self._clock = clock

    def get_snapshot(self) -> DashboardSnapshot:
        worker = self._worker_provider()
        kill_switch = self._kill_switch_provider()
        dependencies = self._dependency_provider()
        with self._session_factory() as session:
            first_event_at = (
                select(func.min(PaperOrderEventModel.occurred_at))
                .where(PaperOrderEventModel.order_id == PaperOrderModel.id)
                .correlate(PaperOrderModel)
                .scalar_subquery()
            )
            orders = tuple(
                OrderView(
                    order_id=str(row.id),
                    symbol=row.symbol,
                    side=row.side.upper(),
                    status=row.status.upper(),
                    quantity=row.requested_volume,
                    filled_quantity=row.filled_volume,
                    created_at=_utc(created_at),
                )
                for row, created_at in session.execute(
                    select(PaperOrderModel, first_event_at)
                    .order_by(first_event_at.desc())
                    .limit(100)
                )
                if created_at is not None
            )
            fills = tuple(
                FillView(
                    fill_id=f"{fill.order_id}:{fill.sequence}",
                    order_id=str(fill.order_id),
                    symbol=order.symbol,
                    quantity=fill.volume,
                    price=fill.price,
                    filled_at=_utc(fill.filled_at),
                )
                for fill, order in session.execute(
                    select(PaperFillModel, PaperOrderModel)
                    .join(PaperOrderModel, PaperOrderModel.id == PaperFillModel.order_id)
                    .order_by(PaperFillModel.filled_at.desc())
                    .limit(100)
                )
            )
            position_rows = tuple(session.scalars(select(PaperPositionModel)))
            positions = tuple(
                PositionView(
                    symbol=row.symbol,
                    net_quantity=row.net_quantity,
                    average_price=row.average_price,
                    unrealized_pnl=row.unrealized_pnl,
                )
                for row in position_rows
                if row.net_quantity != 0
            )
            account_rows = tuple(session.scalars(select(PaperAccountModel)))
            pnl = tuple(_pnl(row) for row in account_rows)
            exposure = tuple(
                _exposure(currency, position_rows)
                for currency in {row.quote_currency for row in position_rows}
            )

        degraded = {
            f"DEPENDENCY_{item.component.upper()}_{item.state.value.upper()}"
            for item in dependencies
            if item.state is not OperationalState.HEALTHY
        }
        if worker.state is not OperationalState.HEALTHY:
            degraded.add("WORKER_UNAVAILABLE")
        if kill_switch.active:
            degraded.add("KILL_SWITCH_ACTIVE")
        return DashboardSnapshot(
            generated_at=_utc(self._clock()),
            mode=TradingMode.PAPER,
            worker=worker,
            kill_switch=kill_switch,
            dependencies=dependencies,
            orders=orders,
            fills=fills,
            positions=positions,
            pnl=pnl,
            exposure=exposure,
            degraded_reason_codes=tuple(sorted(degraded)),
        )

    def get_events_after(self, event_id: int, limit: int) -> tuple[DashboardEvent, ...]:
        """Expose only persisted fill-accounting events; never synthesize runtime evidence."""
        with self._session_factory() as session:
            rows = session.scalars(
                select(PaperAccountingEventModel)
                .where(PaperAccountingEventModel.event_id > event_id)
                .order_by(PaperAccountingEventModel.event_id)
                .limit(limit)
            )
            return tuple(
                DashboardEvent(
                    event_id=row.event_id,
                    occurred_at=_utc(row.recorded_at),
                    kind=DashboardEventKind.PNL,
                    reason_code="PAPER_FILL_ACCOUNTED",
                    entity_id=f"{row.order_id}:{row.fill_sequence}",
                )
                for row in rows
            )

    def get_trades_after(self, event_id: int, limit: int) -> tuple[TradeView, ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(PaperAccountingEventModel)
                .where(PaperAccountingEventModel.event_id > event_id)
                .order_by(PaperAccountingEventModel.event_id)
                .limit(limit)
            )
            return tuple(
                TradeView(
                    event_id=row.event_id,
                    order_id=str(row.order_id),
                    fill_sequence=row.fill_sequence,
                    symbol=row.symbol,
                    side=row.side.upper(),
                    quantity=row.quantity,
                    price=row.price,
                    commission=row.commission,
                    realized_pnl=row.realized_delta,
                    filled_at=_utc(row.filled_at),
                )
                for row in rows
            )

    def get_report(self) -> PaperReport:
        with self._session_factory() as session:
            accounts = tuple(session.scalars(select(PaperAccountModel)))
            if len(accounts) != 1:
                raise ValueError("a single PAPER reporting currency must be configured")
            row = accounts[0]
            fill_count = session.scalar(select(func.count(PaperAccountingEventModel.event_id))) or 0
            open_count = (
                session.scalar(
                    select(func.count(PaperPositionModel.symbol)).where(
                        PaperPositionModel.net_quantity != 0
                    )
                )
                or 0
            )
            return PaperReport(
                generated_at=_utc(self._clock()),
                mode=TradingMode.PAPER,
                currency=row.currency,
                initial_balance=row.initial_balance,
                cash_balance=row.cash_balance,
                realized_pnl=row.realized_pnl,
                unrealized_pnl=row.unrealized_pnl,
                fees=row.fees,
                equity=row.equity,
                equity_peak=row.equity_peak,
                drawdown=row.drawdown,
                drawdown_pct=row.drawdown_pct,
                fill_count=fill_count,
                open_position_count=open_count,
            )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _pnl(row: PaperAccountModel) -> PnlView:
    return PnlView(
        currency=row.currency,
        realized=row.realized_pnl,
        unrealized=row.unrealized_pnl,
        fees=row.fees,
        cash_balance=row.cash_balance,
        equity=row.equity,
        equity_peak=row.equity_peak,
        drawdown=row.drawdown,
        drawdown_pct=row.drawdown_pct,
    )


def _exposure(currency: str, positions: tuple[PaperPositionModel, ...]) -> ExposureView:
    values = tuple(
        row.net_quantity * row.mark_price * row.contract_multiplier
        for row in positions
        if row.quote_currency == currency
    )
    return ExposureView(
        currency=currency,
        gross=sum((abs(value) for value in values), Decimal("0")),
        net=sum(values, Decimal("0")),
    )


__all__ = ["PostgresDashboardRepository"]
