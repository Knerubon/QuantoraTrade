"""Tests for deterministic causal ordering of multi-symbol candle events."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.backtesting.clock import SimulationClock
from quantora_trade.domain.models import Candle

EVENT_TIME = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


def candle(*, symbol: str, timeframe: str, duration: timedelta) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=EVENT_TIME - duration,
        close_time=EVENT_TIME,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        tick_volume=100,
        is_closed=True,
    )


def test_clock_orders_context_before_entry_then_canonical_symbol() -> None:
    xau_m15 = candle(symbol="XAUUSD", timeframe="M15", duration=timedelta(minutes=15))
    eur_m15 = candle(symbol="EURUSD", timeframe="M15", duration=timedelta(minutes=15))
    xau_h1 = candle(symbol="XAUUSD", timeframe="H1", duration=timedelta(hours=1))

    clock = SimulationClock.from_candles((xau_m15, eur_m15, xau_h1))

    identities = tuple((event.candle.symbol, event.candle.timeframe) for event in clock.events)
    assert identities == (("XAUUSD", "H1"), ("EURUSD", "M15"), ("XAUUSD", "M15"))


def test_advance_returns_new_clock_without_mutating_previous_state() -> None:
    clock = SimulationClock.from_candles(
        (candle(symbol="EURUSD", timeframe="M15", duration=timedelta(minutes=15)),)
    )

    event, advanced = clock.advance()

    assert event.occurred_at == EVENT_TIME
    assert clock.cursor == 0
    assert clock.remaining == 1
    assert advanced.cursor == 1
    assert advanced.is_finished
    with pytest.raises(StopIteration, match="exhausted"):
        advanced.advance()


@pytest.mark.parametrize(
    ("invalid", "message"),
    [
        (
            replace(
                candle(symbol="EURUSD", timeframe="M15", duration=timedelta(minutes=15)),
                is_closed=False,
            ),
            "closed candles",
        ),
        (
            candle(symbol="eurusd", timeframe="M15", duration=timedelta(minutes=15)),
            "canonical uppercase",
        ),
        (
            candle(symbol="EURUSD", timeframe="M15", duration=timedelta(minutes=5)),
            "duration",
        ),
    ],
)
def test_clock_rejects_unsafe_candle_inputs(invalid: Candle, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SimulationClock.from_candles((invalid,))


def test_clock_rejects_duplicate_identity_and_empty_dataset() -> None:
    item = candle(symbol="EURUSD", timeframe="M15", duration=timedelta(minutes=15))

    with pytest.raises(ValueError, match="duplicate"):
        SimulationClock.from_candles((item, item))
    with pytest.raises(ValueError, match="at least one"):
        SimulationClock.from_candles(())
