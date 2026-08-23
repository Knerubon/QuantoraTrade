"""Trusted bridge from risk-approved intents to deterministic PAPER execution."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from quantora_trade.domain.enums import TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent
from quantora_trade.domain.ports import BrokerOrderResult, BrokerPort, ClockPort
from quantora_trade.execution.models import (
    InstrumentExecutionSnapshot,
    PaperOrder,
    PaperOrderRequest,
    PaperQuote,
)


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class PaperExecutionInput:
    """One authoritative point-in-time input used to construct a PAPER request."""

    instrument: InstrumentExecutionSnapshot
    quote: PaperQuote
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.expires_at, "expires_at")


class PaperExecutionInputPort(Protocol):
    """Returns trusted instrument and quote data for exactly one approved intent."""

    def execution_input(self, intent: ApprovedOrderIntent) -> PaperExecutionInput: ...


class PaperAdapterPort(Protocol):
    """Narrow local adapter contract used by the PAPER broker bridge."""

    def submit(self, request: PaperOrderRequest, quote: PaperQuote) -> PaperOrder: ...

    def get(self, idempotency_key: str) -> PaperOrder: ...


@dataclass(frozen=True, slots=True)
class PaperBrokerOrderResult:
    """BrokerPort-compatible result retaining the complete PAPER order."""

    order: PaperOrder

    @property
    def external_order_id(self) -> str:
        return str(self.order.id)


class PaperBrokerPort(BrokerPort):
    """PAPER-only BrokerPort with no network, MT5, or Live capability."""

    def __init__(
        self,
        *,
        inputs: PaperExecutionInputPort,
        adapter: PaperAdapterPort,
        clock: ClockPort,
    ) -> None:
        self._inputs = inputs
        self._adapter = adapter
        self._clock = clock

    def submit(self, order: ApprovedOrderIntent) -> BrokerOrderResult:
        """Map an approved intent deterministically and submit it to PAPER only."""

        if not isinstance(order, ApprovedOrderIntent):
            raise TypeError("paper broker accepts ApprovedOrderIntent only")
        if order.mode is not TradingMode.PAPER:
            raise PermissionError("paper broker accepts PAPER mode only")

        now = self._clock.now()
        _require_utc(now, "trusted clock")
        if order.created_at > now:
            raise PermissionError("approved intent cannot come from the future")

        execution_input = self._inputs.execution_input(order)
        if execution_input.quote.symbol != order.symbol:
            raise PermissionError("trusted PAPER input symbol does not match approved intent")
        if execution_input.expires_at <= order.created_at:
            raise PermissionError("trusted PAPER expiry must be after intent creation")

        request = PaperOrderRequest(
            approved_intent_id=order.id,
            idempotency_key=order.idempotency_key,
            mode=TradingMode.PAPER,
            symbol=order.symbol,
            side=order.side,
            volume=order.volume,
            instrument=execution_input.instrument,
            expires_at=execution_input.expires_at,
        )
        return PaperBrokerOrderResult(self._adapter.submit(request, execution_input.quote))

    def get_order(self, idempotency_key: str) -> PaperOrder:
        """Expose retained PAPER state for reconciliation and monitoring."""

        return self._adapter.get(idempotency_key)

    def lookup(self, idempotency_key: str, request_hash: str) -> BrokerOrderResult | None:
        """Recover a prior deterministic outcome without creating a new order."""

        if not idempotency_key.strip() or len(request_hash) != 64:
            raise ValueError("valid idempotency key and request hash are required")
        try:
            order = self._adapter.get(idempotency_key)
        except KeyError:
            return None
        return PaperBrokerOrderResult(order)
