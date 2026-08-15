"""Ports implemented by infrastructure adapters."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from quantora_trade.domain.models import ApprovedOrderIntent, Candle, Instrument


class ClockPort(Protocol):
    """Provides an explicit current time for live and simulated modes."""

    def now(self) -> datetime:
        ...


class MarketDataPort(Protocol):
    """Retrieves broker-normalized market data."""

    def instrument(self, symbol: str) -> Instrument:
        ...

    def closed_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        until: datetime,
        limit: int,
    ) -> Sequence[Candle]:
        ...


class BrokerOrderResult(Protocol):
    """Minimal broker response contract."""

    @property
    def external_order_id(self) -> str:
        ...


class BrokerPort(Protocol):
    """Submits only intents that have already passed deterministic risk checks."""

    def submit(self, order: ApprovedOrderIntent) -> BrokerOrderResult:
        ...


class NotificationPort(Protocol):
    """Sends operational notifications without exposing provider details."""

    def send(self, event_code: str, message: str) -> None:
        ...
