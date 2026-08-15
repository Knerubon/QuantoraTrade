"""Tests for the optional MetaTrader5 SDK wrapper."""

from datetime import UTC, datetime
from types import ModuleType

import pytest

from quantora_trade.infrastructure.mt5.market_data import MetaTrader5Gateway
from quantora_trade.market_data.errors import MT5ConnectionError, UnsupportedTimeframeError


def fake_module(*, initialize_result: bool = True) -> ModuleType:
    module = ModuleType("MetaTrader5")
    module.TIMEFRAME_M15 = 15  # type: ignore[attr-defined]
    module.initialize = lambda: initialize_result  # type: ignore[attr-defined]
    module.shutdown = lambda: None  # type: ignore[attr-defined]
    module.last_error = lambda: (1, "failure")  # type: ignore[attr-defined]
    module.symbol_info = lambda symbol: None  # type: ignore[attr-defined]
    module.copy_rates_range = lambda symbol, timeframe, start, end: (  # type: ignore[attr-defined]
        {
            "time": int(datetime(2026, 8, 15, 12, 0, tzinfo=UTC).timestamp()),
            "open": 1.1,
            "high": 1.2,
            "low": 1.0,
            "close": 1.15,
            "tick_volume": 100,
            "spread": 10,
            "real_volume": 0,
        },
    )
    return module


def test_gateway_initialize_and_shutdown_when_sdk_succeeds() -> None:
    gateway = MetaTrader5Gateway(fake_module())

    gateway.initialize()
    gateway.shutdown()


def test_gateway_initialize_when_sdk_fails_raises_typed_error() -> None:
    gateway = MetaTrader5Gateway(fake_module(initialize_result=False))

    with pytest.raises(MT5ConnectionError, match="initialize"):
        gateway.initialize()


def test_gateway_when_symbol_does_not_exist_returns_none() -> None:
    gateway = MetaTrader5Gateway(fake_module())

    assert gateway.symbol_info("UNKNOWN") is None


def test_gateway_maps_raw_rate_rows() -> None:
    gateway = MetaTrader5Gateway(fake_module())

    rates = gateway.copy_rates_range(
        "EURUSD",
        "M15",
        datetime(2026, 8, 15, 11, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 13, 0, tzinfo=UTC),
    )

    assert len(rates) == 1
    assert str(rates[0].close) == "1.15"


def test_gateway_when_timeframe_is_unknown_raises_typed_error() -> None:
    gateway = MetaTrader5Gateway(fake_module())

    with pytest.raises(UnsupportedTimeframeError, match="M30"):
        gateway.copy_rates_range(
            "EURUSD",
            "M30",
            datetime(2026, 8, 15, 11, 0, tzinfo=UTC),
            datetime(2026, 8, 15, 13, 0, tzinfo=UTC),
        )


def test_gateway_when_copy_rates_fails_raises_typed_error() -> None:
    module = fake_module()
    module.copy_rates_range = lambda symbol, timeframe, start, end: None  # type: ignore[attr-defined]
    gateway = MetaTrader5Gateway(module)

    with pytest.raises(MT5ConnectionError, match="copy_rates_range"):
        gateway.copy_rates_range(
            "EURUSD",
            "M15",
            datetime(2026, 8, 15, 11, 0, tzinfo=UTC),
            datetime(2026, 8, 15, 13, 0, tzinfo=UTC),
        )
