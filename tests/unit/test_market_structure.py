"""Tests for causal support/resistance structure."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.domain.models import Candle
from quantora_trade.strategy.market_structure import (
    MarketStructureConfig,
    StructureKind,
    calculate_market_structure,
)

START = datetime(2026, 8, 16, 0, 0, tzinfo=UTC)
CONFIG = MarketStructureConfig(left_bars=2, right_bars=2, zone_tolerance=Decimal("0.01"))


def candle(index: int, *, high: str, low: str, symbol: str = "EURUSD") -> Candle:
    midpoint = (Decimal(high) + Decimal(low)) / Decimal("2")
    return Candle(
        symbol=symbol,
        timeframe="M15",
        open_time=START + timedelta(minutes=15 * index),
        close_time=START + timedelta(minutes=15 * (index + 1)),
        open=midpoint,
        high=Decimal(high),
        low=Decimal(low),
        close=midpoint,
        tick_volume=100,
        is_closed=True,
    )


def structure_candles() -> tuple[Candle, ...]:
    return (
        candle(0, high="10", low="6"),
        candle(1, high="11", low="5"),
        candle(2, high="12", low="4"),
        candle(3, high="11", low="5"),
        candle(4, high="10", low="6"),
        candle(5, high="9", low="7"),
    )


def test_market_structure_confirms_support_and_resistance_after_right_bars() -> None:
    result = calculate_market_structure(structure_candles(), CONFIG)

    assert result[3].swings == ()
    assert {swing.kind for swing in result[4].swings} == {
        StructureKind.SUPPORT,
        StructureKind.RESISTANCE,
    }
    assert all(swing.occurred_at == structure_candles()[2].close_time for swing in result[4].swings)
    assert all(
        swing.confirmed_at == structure_candles()[4].close_time for swing in result[4].swings
    )
    assert len(result[4].zones) == 2


def test_market_structure_does_not_rewrite_past_when_future_is_appended() -> None:
    history = structure_candles()

    prefix = calculate_market_structure(history[:5], CONFIG)
    complete = calculate_market_structure(history, CONFIG)

    assert prefix == complete[:5]


def test_market_structure_keeps_symbols_independent() -> None:
    eurusd = calculate_market_structure(structure_candles(), CONFIG)
    xauusd_history = tuple(
        candle(index, high=str(item.high), low=str(item.low), symbol="XAUUSD")
        for index, item in enumerate(structure_candles())
    )
    xauusd = calculate_market_structure(xauusd_history, CONFIG)

    assert all(point.symbol == "EURUSD" for point in eurusd)
    assert all(point.symbol == "XAUUSD" for point in xauusd)


def test_market_structure_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="pivot bars"):
        MarketStructureConfig(left_bars=0)
    with pytest.raises(ValueError, match="zone_tolerance"):
        MarketStructureConfig(zone_tolerance=Decimal("1"))
