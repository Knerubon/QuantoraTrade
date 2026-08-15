"""Tests for MT5 normalization without a terminal connection."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.domain.enums import AssetClass
from quantora_trade.infrastructure.mt5.market_data import MT5MarketDataAdapter
from quantora_trade.market_data.errors import SymbolNotFoundError, UnsupportedTimeframeError
from quantora_trade.market_data.gateway import MT5Gateway, MT5Rate, MT5SymbolInfo

NOW = datetime(2026, 8, 15, 12, 30, tzinfo=UTC)


class FakeGateway(MT5Gateway):
    def __init__(
        self,
        *,
        info: MT5SymbolInfo | None,
        rates: Sequence[MT5Rate] = (),
    ) -> None:
        self.info = info
        self.rates = rates
        self.requests: list[tuple[str, str, datetime, datetime]] = []

    def initialize(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def symbol_info(self, symbol: str) -> MT5SymbolInfo | None:
        return self.info

    def copy_rates_range(
        self,
        symbol: str,
        timeframe: str,
        date_from: datetime,
        date_to: datetime,
    ) -> Sequence[MT5Rate]:
        self.requests.append((symbol, timeframe, date_from, date_to))
        return self.rates


def symbol_info(*, currency_base: str | None = "EUR") -> MT5SymbolInfo:
    return MT5SymbolInfo(
        symbol="EURUSD" if currency_base else "XAUUSD",
        path="Forex\\Majors" if currency_base else "Metals",
        currency_base=currency_base,
        currency_profit="USD",
        digits=5 if currency_base else 2,
        point=Decimal("0.00001") if currency_base else Decimal("0.01"),
        trade_tick_size=Decimal("0.00001") if currency_base else Decimal("0.01"),
        trade_tick_value=Decimal("1"),
        trade_contract_size=Decimal("100000") if currency_base else Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
    )


def rate(open_time: datetime) -> MT5Rate:
    return MT5Rate(
        epoch_seconds=int(open_time.timestamp()),
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=Decimal("1.1005"),
        tick_volume=100,
        spread_points=12,
        real_volume=0,
    )


def test_adapter_maps_forex_symbol_specification() -> None:
    adapter = MT5MarketDataAdapter(FakeGateway(info=symbol_info()))

    instrument = adapter.instrument("EURUSD")

    assert instrument.symbol == "EURUSD"
    assert instrument.asset_class is AssetClass.FOREX
    assert instrument.tick_size == Decimal("0.00001")


def test_adapter_maps_non_currency_symbol_to_metal() -> None:
    adapter = MT5MarketDataAdapter(FakeGateway(info=symbol_info(currency_base=None)))

    instrument = adapter.instrument("XAUUSD")

    assert instrument.asset_class is AssetClass.METAL
    assert instrument.contract_size == Decimal("100")


def test_adapter_when_symbol_is_missing_raises_typed_error() -> None:
    adapter = MT5MarketDataAdapter(FakeGateway(info=None))

    with pytest.raises(SymbolNotFoundError, match="UNKNOWN"):
        adapter.instrument("UNKNOWN")


def test_adapter_returns_only_closed_candles_and_applies_limit() -> None:
    gateway = FakeGateway(
        info=symbol_info(),
        rates=(
            rate(NOW - timedelta(minutes=45)),
            rate(NOW - timedelta(minutes=30)),
            rate(NOW - timedelta(minutes=15)),
            rate(NOW),
        ),
    )
    adapter = MT5MarketDataAdapter(gateway)

    candles = adapter.closed_candles("EURUSD", "M15", until=NOW, limit=2)

    assert len(candles) == 2
    assert all(item.is_closed for item in candles)
    assert candles[-1].close_time == NOW
    assert gateway.requests[0][0:2] == ("EURUSD", "M15")


def test_adapter_when_until_is_naive_rejects_request() -> None:
    adapter = MT5MarketDataAdapter(FakeGateway(info=symbol_info()))

    with pytest.raises(ValueError, match="UTC"):
        adapter.closed_candles(
            "EURUSD",
            "M15",
            until=datetime(2026, 8, 15, 12, 30),
            limit=10,
        )


def test_adapter_when_limit_is_invalid_rejects_request() -> None:
    adapter = MT5MarketDataAdapter(FakeGateway(info=symbol_info()))

    with pytest.raises(ValueError, match="limit"):
        adapter.closed_candles("EURUSD", "M15", until=NOW, limit=0)


def test_adapter_when_timeframe_is_unknown_raises_typed_error() -> None:
    adapter = MT5MarketDataAdapter(FakeGateway(info=symbol_info()))

    with pytest.raises(UnsupportedTimeframeError, match="M30"):
        adapter.closed_candles("EURUSD", "M30", until=NOW, limit=10)
