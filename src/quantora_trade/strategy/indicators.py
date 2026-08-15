"""Decimal-based indicators calculated only from closed candle history."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from quantora_trade.domain.models import Candle
from quantora_trade.strategy.validation import validate_closed_candle_series

HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class IndicatorConfig:
    """Periods for the Phase 2 baseline indicator set."""

    ema_fast: int = 9
    ema_mid: int = 21
    ema_slow: int = 50
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14

    def __post_init__(self) -> None:
        for name, value in (
            ("ema_fast", self.ema_fast),
            ("ema_mid", self.ema_mid),
            ("ema_slow", self.ema_slow),
            ("rsi_period", self.rsi_period),
            ("macd_fast", self.macd_fast),
            ("macd_slow", self.macd_slow),
            ("macd_signal", self.macd_signal),
            ("atr_period", self.atr_period),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("macd_fast must be less than macd_slow")


@dataclass(frozen=True, slots=True)
class TechnicalIndicatorPoint:
    """Indicator values known at one closed candle's close time."""

    symbol: str
    timeframe: str
    observed_at: datetime
    ema_fast: Decimal | None
    ema_mid: Decimal | None
    ema_slow: Decimal | None
    rsi: Decimal | None
    macd: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None
    atr: Decimal | None


def _ema(values: tuple[Decimal, ...], period: int) -> tuple[Decimal | None, ...]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    current = sum(values[:period], Decimal("0")) / Decimal(period)
    result[period - 1] = current
    alpha = Decimal("2") / Decimal(period + 1)
    for index in range(period, len(values)):
        current = ((values[index] - current) * alpha) + current
        result[index] = current
    return tuple(result)


def _rsi(values: tuple[Decimal, ...], period: int) -> tuple[Decimal | None, ...]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) <= period:
        return tuple(result)
    changes = tuple(values[index] - values[index - 1] for index in range(1, len(values)))
    average_gain = sum(
        (max(change, Decimal("0")) for change in changes[:period]), Decimal("0")
    ) / Decimal(period)
    average_loss = sum(
        (max(-change, Decimal("0")) for change in changes[:period]), Decimal("0")
    ) / Decimal(period)

    def value() -> Decimal:
        if average_loss == 0:
            return HUNDRED if average_gain > 0 else Decimal("50")
        relative_strength = average_gain / average_loss
        return HUNDRED - (HUNDRED / (Decimal("1") + relative_strength))

    result[period] = value()
    for index in range(period + 1, len(values)):
        change = changes[index - 1]
        average_gain = ((average_gain * Decimal(period - 1)) + max(change, Decimal("0"))) / Decimal(
            period
        )
        average_loss = (
            (average_loss * Decimal(period - 1)) + max(-change, Decimal("0"))
        ) / Decimal(period)
        result[index] = value()
    return tuple(result)


def _macd(
    values: tuple[Decimal, ...],
    fast_period: int,
    slow_period: int,
    signal_period: int,
) -> tuple[
    tuple[Decimal | None, ...],
    tuple[Decimal | None, ...],
    tuple[Decimal | None, ...],
]:
    fast = _ema(values, fast_period)
    slow = _ema(values, slow_period)
    line: list[Decimal | None] = [None] * len(values)
    defined: list[Decimal] = []
    defined_indexes: list[int] = []
    for index, (fast_value, slow_value) in enumerate(zip(fast, slow, strict=True)):
        if fast_value is not None and slow_value is not None:
            macd_value = fast_value - slow_value
            line[index] = macd_value
            defined.append(macd_value)
            defined_indexes.append(index)
    compact_signal = _ema(tuple(defined), signal_period)
    signal: list[Decimal | None] = [None] * len(values)
    histogram: list[Decimal | None] = [None] * len(values)
    for compact_index, source_index in enumerate(defined_indexes):
        signal_value = compact_signal[compact_index]
        signal[source_index] = signal_value
        if signal_value is not None:
            line_value = line[source_index]
            assert line_value is not None
            histogram[source_index] = line_value - signal_value
    return tuple(line), tuple(signal), tuple(histogram)


def _atr(candles: tuple[Candle, ...], period: int) -> tuple[Decimal | None, ...]:
    true_ranges: list[Decimal] = []
    for index, candle in enumerate(candles):
        if index == 0:
            true_ranges.append(candle.high - candle.low)
            continue
        previous_close = candles[index - 1].close
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
    result: list[Decimal | None] = [None] * len(candles)
    if len(true_ranges) < period:
        return tuple(result)
    current = sum(true_ranges[:period], Decimal("0")) / Decimal(period)
    result[period - 1] = current
    for index in range(period, len(true_ranges)):
        current = ((current * Decimal(period - 1)) + true_ranges[index]) / Decimal(period)
        result[index] = current
    return tuple(result)


def calculate_indicators(
    candles: tuple[Candle, ...],
    config: IndicatorConfig | None = None,
) -> tuple[TechnicalIndicatorPoint, ...]:
    """Calculate a causal series; each point uses its candle and earlier history only."""

    validate_closed_candle_series(candles)
    config = config or IndicatorConfig()
    closes = tuple(candle.close for candle in candles)
    ema_fast = _ema(closes, config.ema_fast)
    ema_mid = _ema(closes, config.ema_mid)
    ema_slow = _ema(closes, config.ema_slow)
    rsi = _rsi(closes, config.rsi_period)
    macd, macd_signal, macd_histogram = _macd(
        closes,
        config.macd_fast,
        config.macd_slow,
        config.macd_signal,
    )
    atr = _atr(candles, config.atr_period)
    return tuple(
        TechnicalIndicatorPoint(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            observed_at=candle.close_time,
            ema_fast=ema_fast[index],
            ema_mid=ema_mid[index],
            ema_slow=ema_slow[index],
            rsi=rsi[index],
            macd=macd[index],
            macd_signal=macd_signal[index],
            macd_histogram=macd_histogram[index],
            atr=atr[index],
        )
        for index, candle in enumerate(candles)
    )
