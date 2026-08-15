"""Tests for fail-closed market-data orchestration."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.domain.enums import AssetClass
from quantora_trade.domain.models import Candle, Instrument
from quantora_trade.domain.ports import MarketDataPort
from quantora_trade.market_data.errors import DataQualityError
from quantora_trade.market_data.quality import MarketDataValidator
from quantora_trade.market_data.service import MarketDataService

NOW = datetime(2026, 8, 15, 12, 30, tzinfo=UTC)


class FakeMarketDataPort(MarketDataPort):
    def __init__(self, candles: Sequence[Candle]) -> None:
        self.candles = candles

    def instrument(self, symbol: str) -> Instrument:
        return Instrument(
            symbol=symbol,
            asset_class=AssetClass.FOREX,
            quote_currency="USD",
            digits=5,
            point=Decimal("0.00001"),
            pip_size=Decimal("0.0001"),
            tick_size=Decimal("0.00001"),
            tick_value=Decimal("1"),
            contract_size=Decimal("100000"),
            spread_points=12,
            session_timezone="UTC",
            session_profile="forex_24x5",
            volume_min=Decimal("0.01"),
            volume_max=Decimal("100"),
            volume_step=Decimal("0.01"),
        )

    def closed_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        until: datetime,
        limit: int,
    ) -> Sequence[Candle]:
        return self.candles[-limit:]


def valid_candle() -> Candle:
    return Candle(
        symbol="EURUSD",
        timeframe="M15",
        open_time=NOW - timedelta(minutes=15),
        close_time=NOW,
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=Decimal("1.1005"),
        tick_volume=100,
        is_closed=True,
    )


def validator() -> MarketDataValidator:
    return MarketDataValidator(
        expected_interval=timedelta(minutes=15),
        max_staleness=timedelta(minutes=20),
    )


def test_service_when_data_is_valid_returns_snapshot() -> None:
    service = MarketDataService(FakeMarketDataPort((valid_candle(),)))

    snapshot = service.load_validated_snapshot(
        symbol="EURUSD",
        timeframe="M15",
        until=NOW,
        limit=100,
        validator=validator(),
    )

    assert snapshot.quality.is_usable is True
    assert snapshot.instrument.symbol == "EURUSD"
    assert len(snapshot.candles) == 1


def test_service_when_data_is_empty_fails_closed() -> None:
    service = MarketDataService(FakeMarketDataPort(()))

    with pytest.raises(DataQualityError, match="EMPTY_DATA"):
        service.load_validated_snapshot(
            symbol="EURUSD",
            timeframe="M15",
            until=NOW,
            limit=100,
            validator=validator(),
        )


def test_service_when_limit_is_invalid_rejects_request() -> None:
    service = MarketDataService(FakeMarketDataPort((valid_candle(),)))

    with pytest.raises(ValueError, match="limit"):
        service.load_validated_snapshot(
            symbol="EURUSD",
            timeframe="M15",
            until=NOW,
            limit=0,
            validator=validator(),
        )
