"""Bounded, PAPER-only execution cycle composition."""

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from quantora_trade.domain.enums import TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent
from quantora_trade.domain.ports import BrokerOrderResult
from quantora_trade.execution.models import OrderStatus, PaperOrder
from quantora_trade.execution.service import PaperBrokerOrderResult
from quantora_trade.notifications.models import AlertEvent, AlertSeverity
from quantora_trade.risk.submission import OrderSubmissionService


class PaperRuntimeEventKind(StrEnum):
    ACCEPTED = "accepted"
    FILL = "fill"
    REJECTED = "rejected"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class PaperRuntimeEvent:
    kind: PaperRuntimeEventKind
    idempotency_key: str
    occurred_at: datetime
    order: PaperOrder | None
    code: str


class PaperRuntimeEventPort(Protocol):
    def emit(self, event: PaperRuntimeEvent) -> None: ...


class PaperRuntimeProjectionError(RuntimeError):
    """Execution succeeded but mandatory local projection needs reconciliation."""


class AlertPublisher(Protocol):
    def publish(self, event: AlertEvent, *, now: datetime) -> object: ...


class RuntimeClock(Protocol):
    def now(self) -> datetime: ...


class PaperRuntime:
    """Execute at most one already-approved intent per explicit invocation."""

    def __init__(
        self,
        *,
        submissions: OrderSubmissionService,
        events: PaperRuntimeEventPort,
        alerts: AlertPublisher,
        clock: RuntimeClock,
    ) -> None:
        self._submissions = submissions
        self._events = events
        self._alerts = alerts
        self._clock = clock

    def run_once(self, intent: ApprovedOrderIntent) -> BrokerOrderResult:
        if not isinstance(intent, ApprovedOrderIntent):
            raise TypeError("runtime accepts ApprovedOrderIntent only")
        if intent.mode is not TradingMode.PAPER:
            raise PermissionError("runtime is restricted to PAPER mode")
        now = self._now()
        try:
            result = self._submissions.submit(intent)
            if isinstance(result, PaperBrokerOrderResult):
                try:
                    self._emit_order(result.order, now)
                except Exception as error:
                    self._alert(intent.idempotency_key, now, type(error).__name__)
                    raise PaperRuntimeProjectionError(
                        "PAPER outcome requires projection reconciliation"
                    ) from error
            return result
        except PaperRuntimeProjectionError:
            raise
        except Exception as error:
            with suppress(Exception):
                self._emit(
                    PaperRuntimeEventKind.CRITICAL,
                    intent.idempotency_key,
                    now,
                    None,
                    type(error).__name__,
                )
            self._alert(intent.idempotency_key, now, type(error).__name__)
            raise

    def _emit_order(self, order: PaperOrder, now: datetime) -> None:
        if any(event.status is OrderStatus.ACCEPTED for event in order.events):
            self._emit(
                PaperRuntimeEventKind.ACCEPTED,
                order.request.idempotency_key,
                now,
                order,
                "ORDER_ACCEPTED",
            )
        if order.fills:
            self._emit(
                PaperRuntimeEventKind.FILL,
                order.request.idempotency_key,
                now,
                order,
                order.events[-1].code,
            )
        if order.status is OrderStatus.REJECTED:
            self._emit(
                PaperRuntimeEventKind.REJECTED,
                order.request.idempotency_key,
                now,
                order,
                order.events[-1].code,
            )

    def _emit(
        self,
        kind: PaperRuntimeEventKind,
        key: str,
        now: datetime,
        order: PaperOrder | None,
        code: str,
    ) -> None:
        self._events.emit(PaperRuntimeEvent(kind, key, now, order, code))

    def _alert(self, key: str, now: datetime, code: str) -> None:
        event = AlertEvent(
            event_code="PAPER_RUNTIME_CRITICAL",
            severity=AlertSeverity.CRITICAL,
            component="paper-runtime",
            message="PAPER execution cycle failed",
            dedup_key=f"paper-runtime:{key}:{code}",
            cooldown=timedelta(minutes=5),
            occurred_at=now,
            payload=MappingProxyType({"idempotency_key": key, "error_type": code}),
        )
        # Provider failures are observability failures, not execution state.
        with suppress(Exception):
            self._alerts.publish(event, now=now)

    def _now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("runtime clock must return timezone-aware UTC")
        return now


__all__ = [
    "AlertPublisher",
    "PaperRuntime",
    "PaperRuntimeEvent",
    "PaperRuntimeEventKind",
    "PaperRuntimeEventPort",
    "PaperRuntimeProjectionError",
]
