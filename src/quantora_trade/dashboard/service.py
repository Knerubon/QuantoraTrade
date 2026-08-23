"""Read-only dashboard query service with deterministic event paging."""

from typing import Protocol

from quantora_trade.dashboard.models import (
    DashboardEvent,
    DashboardEventPage,
    DashboardSnapshot,
    PaperReport,
    TradePage,
    TradeView,
)


class DashboardRepository(Protocol):
    """Persistence boundary exposing sanitized dashboard DTOs only."""

    def get_snapshot(self) -> DashboardSnapshot: ...

    def get_events_after(self, event_id: int, limit: int) -> tuple[DashboardEvent, ...]: ...

    def get_trades_after(self, event_id: int, limit: int) -> tuple[TradeView, ...]: ...

    def get_report(self) -> PaperReport: ...


class DashboardService:
    def __init__(self, repository: DashboardRepository) -> None:
        self._repository = repository

    def snapshot(self) -> DashboardSnapshot:
        return self._repository.get_snapshot()

    def events(self, *, after_event_id: int, limit: int) -> DashboardEventPage:
        if after_event_id < 0:
            raise ValueError("after_event_id must not be negative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        events = self._repository.get_events_after(after_event_id, limit)
        ordered = tuple(sorted(events, key=lambda event: event.event_id))
        if len(ordered) > limit:
            raise ValueError("repository returned more events than requested")
        if any(event.event_id <= after_event_id for event in ordered):
            raise ValueError("repository returned an event at or before the cursor")
        if len({event.event_id for event in ordered}) != len(ordered):
            raise ValueError("repository returned duplicate event IDs")
        next_cursor = ordered[-1].event_id if ordered else after_event_id
        return DashboardEventPage(events=ordered, next_cursor=next_cursor)

    def trades(self, *, after_event_id: int, limit: int) -> TradePage:
        if after_event_id < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid trade cursor or limit")
        trades = tuple(
            sorted(
                self._repository.get_trades_after(after_event_id, limit),
                key=lambda item: item.event_id,
            )
        )
        if len(trades) > limit or any(item.event_id <= after_event_id for item in trades):
            raise ValueError("repository violated the trade paging contract")
        if len({item.event_id for item in trades}) != len(trades):
            raise ValueError("repository returned duplicate trade event IDs")
        return TradePage(
            trades=trades,
            next_cursor=trades[-1].event_id if trades else after_event_id,
        )

    def report(self) -> PaperReport:
        report = self._repository.get_report()
        if report.mode.value != "paper":
            raise ValueError("dashboard report must remain PAPER-only")
        return report
