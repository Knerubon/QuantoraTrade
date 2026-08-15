"""Tests for deterministic candlestick pattern classification."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.domain.models import Candle
from quantora_trade.strategy.patterns import (
    CandlestickPattern,
    PatternConfig,
    detect_candlestick_patterns,
)

START = datetime(2026, 8, 16, 0, 0, tzinfo=UTC)


def candle(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    return Candle(
        symbol="EURUSD",
        timeframe="M15",
        open_time=START + timedelta(minutes=15 * index),
        close_time=START + timedelta(minutes=15 * (index + 1)),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        tick_volume=100,
        is_closed=True,
    )


def test_patterns_detect_doji_hammer_and_shooting_star() -> None:
    history = (
        candle(0, open_="10", high="11", low="9", close="10.05"),
        candle(1, open_="10", high="10.5", low="7", close="10.4"),
        candle(2, open_="10", high="13", low="9.5", close="9.6"),
    )

    result = detect_candlestick_patterns(history)

    assert CandlestickPattern.DOJI in result[0].patterns
    assert CandlestickPattern.HAMMER in result[1].patterns
    assert CandlestickPattern.SHOOTING_STAR in result[2].patterns


def test_patterns_detect_bullish_and_bearish_engulfing() -> None:
    history = (
        candle(0, open_="10", high="10.2", low="8.8", close="9"),
        candle(1, open_="8.5", high="10.7", low="8.3", close="10.5"),
        candle(2, open_="11", high="11.2", low="8.2", close="8.4"),
    )

    result = detect_candlestick_patterns(history)

    assert CandlestickPattern.BULLISH_ENGULFING in result[1].patterns
    assert CandlestickPattern.BEARISH_ENGULFING in result[2].patterns


def test_patterns_do_not_change_past_when_future_is_appended() -> None:
    history = (
        candle(0, open_="10", high="11", low="9", close="10.05"),
        candle(1, open_="10", high="10.5", low="7", close="10.4"),
        candle(2, open_="10", high="13", low="9.5", close="9.6"),
    )

    prefix = detect_candlestick_patterns(history[:2])
    complete = detect_candlestick_patterns(history)

    assert prefix == complete[:2]


def test_pattern_config_rejects_invalid_thresholds() -> None:
    with pytest.raises(ValueError, match="doji_body_ratio"):
        PatternConfig(doji_body_ratio=Decimal("0"))
    with pytest.raises(ValueError, match="wick_to_body_ratio"):
        PatternConfig(wick_to_body_ratio=Decimal("1"))
