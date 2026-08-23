from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from quantora_trade.api.app import create_app
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
from quantora_trade.dashboard.router import create_dashboard_router
from quantora_trade.dashboard.service import DashboardService
from quantora_trade.domain.enums import TradingMode

NOW = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)


def snapshot() -> DashboardSnapshot:
    return DashboardSnapshot(
        generated_at=NOW,
        mode=TradingMode.PAPER,
        worker=WorkerView(
            worker_id="paper-1", state=OperationalState.HEALTHY, last_heartbeat_at=NOW
        ),
        kill_switch=KillSwitchView(active=True, scope="GLOBAL", reason_code="OWNER_HOLD"),
        dependencies=(
            DependencyView(
                component="market_data",
                state=OperationalState.HEALTHY,
                last_success_at=NOW,
                age_seconds=0,
            ),
        ),
        orders=(
            OrderView(
                order_id="order-1",
                symbol="XAUUSD",
                side="BUY",
                status="FILLED",
                quantity=Decimal("0.01"),
                filled_quantity=Decimal("0.01"),
                created_at=NOW,
            ),
        ),
        fills=(
            FillView(
                fill_id="fill-1",
                order_id="order-1",
                symbol="XAUUSD",
                quantity=Decimal("0.01"),
                price=Decimal("2500"),
                filled_at=NOW,
            ),
        ),
        positions=(
            PositionView(
                symbol="XAUUSD",
                net_quantity=Decimal("0.01"),
                average_price=Decimal("2500"),
                unrealized_pnl=Decimal("1.25"),
            ),
        ),
        pnl=(
            PnlView(
                currency="USD",
                realized=Decimal("0"),
                unrealized=Decimal("1.25"),
                fees=Decimal("0.10"),
            ),
        ),
        exposure=(ExposureView(currency="USD", gross=Decimal("25"), net=Decimal("25")),),
        degraded_reason_codes=(),
    )


def event(event_id: int) -> DashboardEvent:
    return DashboardEvent(
        event_id=event_id,
        occurred_at=NOW,
        kind=DashboardEventKind.ORDER,
        reason_code="ORDER_UPDATED",
        entity_id=f"order-{event_id}",
    )


class FakeRepository:
    def __init__(self) -> None:
        self.events = (event(3), event(1), event(2))

    def get_snapshot(self) -> DashboardSnapshot:
        return snapshot()

    def get_events_after(self, event_id: int, limit: int) -> tuple[DashboardEvent, ...]:
        return tuple(item for item in self.events if item.event_id > event_id)[:limit]

    def get_trades_after(self, event_id: int, limit: int) -> tuple[TradeView, ...]:
        trades = (
            TradeView(
                event_id=1,
                order_id="order-1",
                fill_sequence=1,
                symbol="XAUUSD",
                side="BUY",
                quantity=Decimal("0.01"),
                price=Decimal("2500"),
                commission=Decimal("0.10"),
                realized_pnl=Decimal("0"),
                filled_at=NOW,
            ),
        )
        return tuple(item for item in trades if item.event_id > event_id)[:limit]

    def get_report(self) -> PaperReport:
        return PaperReport(
            generated_at=NOW,
            mode=TradingMode.PAPER,
            currency="USD",
            initial_balance=Decimal("10000"),
            cash_balance=Decimal("9999.90"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("1.25"),
            fees=Decimal("0.10"),
            equity=Decimal("10001.15"),
            equity_peak=Decimal("10002"),
            drawdown=Decimal("0.85"),
            drawdown_pct=Decimal("0.00008498"),
            fill_count=1,
            open_position_count=1,
        )


def client(repository: FakeRepository | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_dashboard_router(
            DashboardService(repository or FakeRepository()),
            authorize_read=lambda authorization: (
                "reader"
                if authorization == "Bearer paper-token"
                else (_ for _ in ()).throw(PermissionError("denied"))
            ),
        )
    )
    return TestClient(app)


def read_headers() -> dict[str, str]:
    return {"Authorization": "Bearer paper-token"}


def test_dashboard_is_sanitized_explicit_read_model() -> None:
    response = client().get("/dashboard", headers=read_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "paper"
    assert body["kill_switch"]["active"] is True
    assert body["orders"][0]["symbol"] == "XAUUSD"
    serialized = response.text.lower()
    assert "password" not in serialized
    assert "token" not in serialized
    assert "secret" not in serialized


def test_dashboard_router_is_composed_into_control_api_when_injected() -> None:
    app = create_app(
        dashboard_service=DashboardService(FakeRepository()),
        authorizer=type("Reader", (), {"authorize": lambda self, token, scope: "reader"})(),
    )

    response = TestClient(app).get("/dashboard", headers=read_headers())

    assert response.status_code == 200
    assert response.json()["mode"] == "paper"


def test_events_are_sorted_and_cursor_is_resumable() -> None:
    response = client().get("/events?cursor=1&limit=2", headers=read_headers())

    assert response.status_code == 200
    assert [item["event_id"] for item in response.json()["events"]] == [2, 3]
    assert response.json()["next_cursor"] == 3

    empty = client().get("/events", headers={**read_headers(), "Last-Event-ID": "3"})
    assert empty.json() == {"events": [], "next_cursor": 3}


@pytest.mark.parametrize("header", ["nope", "-1"])
def test_events_reject_invalid_last_event_id(header: str) -> None:
    response = client().get("/events", headers={**read_headers(), "Last-Event-ID": header})
    assert response.status_code == 400


def test_events_reject_ambiguous_conflicting_cursors() -> None:
    response = client().get("/events?cursor=1", headers={**read_headers(), "Last-Event-ID": "2"})
    assert response.status_code == 409


def test_authenticated_sse_is_bounded_resumable_and_heartbeats_when_empty() -> None:
    first = client().get("/events/stream?cursor=1&limit=1", headers=read_headers())
    empty = client().get("/events/stream", headers={**read_headers(), "Last-Event-ID": "3"})

    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/event-stream")
    assert "id: 3" in first.text
    assert '"entity_id":"order-3"' in first.text
    assert empty.text == ": heartbeat\n\n"
    assert client().get("/events/stream").status_code == 403


def test_trades_and_report_are_authenticated_read_only_accounting_views() -> None:
    trades = client().get("/trades", headers=read_headers())
    report = client().get("/report", headers=read_headers())

    assert trades.status_code == 200
    assert trades.json()["trades"][0]["symbol"] == "XAUUSD"
    assert report.status_code == 200
    assert report.json()["mode"] == "paper"
    assert report.json()["drawdown"] == "0.85"
    assert client().get("/trades").status_code == 403
    assert client().get("/report").status_code == 403


def test_dashboard_and_events_fail_closed_without_injected_or_valid_auth() -> None:
    app = FastAPI()
    app.include_router(create_dashboard_router(DashboardService(FakeRepository())))
    unconfigured = TestClient(app)

    assert unconfigured.get("/dashboard").status_code == 403
    assert unconfigured.get("/events").status_code == 403
    assert client().get("/dashboard").status_code == 403


def test_service_rejects_repository_cursor_contract_violation() -> None:
    class BrokenRepository(FakeRepository):
        def get_events_after(self, event_id: int, limit: int) -> tuple[DashboardEvent, ...]:
            return (event(1),)

    with pytest.raises(ValueError, match="at or before"):
        DashboardService(BrokenRepository()).events(after_event_id=1, limit=10)


def test_snapshot_rejects_unsorted_or_secret_extra_fields() -> None:
    data = snapshot().model_dump()
    data["degraded_reason_codes"] = ("Z", "A", "Z")
    with pytest.raises(ValidationError, match="unique and sorted"):
        DashboardSnapshot.model_validate(data)

    worker = WorkerView.model_validate
    with pytest.raises(ValidationError, match="Extra inputs"):
        worker(
            {
                "worker_id": "paper-1",
                "state": "healthy",
                "last_heartbeat_at": NOW,
                "api_token": "must-never-be-exposed",
            }
        )
