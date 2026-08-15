"""Tests for deterministic, causal technical indicators."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.domain.models import Candle
from quantora_trade.strategy.indicators import IndicatorConfig, calculate_indicators

START = datetime(2026, 8, 16, 0, 0, tzinfo=UTC)
SMALL_CONFIG = IndicatorConfig(
    ema_fast=3,
    ema_mid=3,
    ema_slow=3,
    rsi_period=3,
    macd_fast=2,
    macd_slow=3,
    macd_signal=2,
    atr_period=3,
)


def candles(*closes: str, symbol: str = "EURUSD") -> tuple[Candle, ...]:
    return tuple(
        Candle(
            symbol=symbol,
            timeframe="M15",
            open_time=START + timedelta(minutes=15 * index),
            close_time=START + timedelta(minutes=15 * (index + 1)),
            open=Decimal(close),
            high=Decimal(close) + Decimal("1"),
            low=Decimal(close) - Decimal("1"),
            close=Decimal(close),
            tick_volume=100,
            is_closed=True,
        )
        for index, close in enumerate(closes)
    )


def test_calculate_indicators_produces_expected_ema_rsi_and_atr() -> None:
    result = calculate_indicators(candles("1", "2", "3", "4", "5"), SMALL_CONFIG)

    assert tuple(point.ema_fast for point in result) == (
        None,
        None,
        Decimal("2"),
        Decimal("3"),
        Decimal("4"),
    )
    assert result[3].rsi == Decimal("100")
    assert result[2].atr == Decimal("2")
    assert result[-1].macd is not None
    assert result[-1].macd_signal is not None
    assert result[-1].macd_histogram is not None


def test_calculate_indicators_when_prices_are_flat_sets_neutral_rsi() -> None:
    result = calculate_indicators(candles("2", "2", "2", "2"), SMALL_CONFIG)

    assert result[-1].rsi == Decimal("50")


def test_calculate_indicators_does_not_change_past_when_future_is_appended() -> None:
    history = candles("1", "2", "3", "4", "5", "6")

    prefix = calculate_indicators(history[:5], SMALL_CONFIG)
    complete = calculate_indicators(history, SMALL_CONFIG)

    assert prefix == complete[:5]


def test_calculate_indicators_keeps_symbol_series_independent() -> None:
    eurusd = calculate_indicators(candles("1", "2", "3", "4"), SMALL_CONFIG)
    xauusd = calculate_indicators(candles("10", "11", "12", "13", symbol="XAUUSD"), SMALL_CONFIG)

    assert all(point.symbol == "EURUSD" for point in eurusd)
    assert all(point.symbol == "XAUUSD" for point in xauusd)


def test_calculate_indicators_rejects_forming_or_mixed_candles() -> None:
    valid = candles("1", "2", "3")
    forming = Candle(
        symbol="EURUSD",
        timeframe="M15",
        open_time=START + timedelta(minutes=45),
        close_time=START + timedelta(minutes=60),
        open=Decimal("4"),
        high=Decimal("5"),
        low=Decimal("3"),
        close=Decimal("4"),
        tick_volume=100,
        is_closed=False,
    )

    with pytest.raises(ValueError, match="closed"):
        calculate_indicators((*valid, forming), SMALL_CONFIG)
    with pytest.raises(ValueError, match="symbol and timeframe"):
        calculate_indicators((*valid, candles("4", symbol="XAUUSD")[0]), SMALL_CONFIG)


def test_indicator_config_rejects_invalid_periods() -> None:
    with pytest.raises(ValueError, match="ema_fast"):
        IndicatorConfig(ema_fast=0)
    with pytest.raises(ValueError, match="macd_fast"):
        IndicatorConfig(macd_fast=26, macd_slow=12)
