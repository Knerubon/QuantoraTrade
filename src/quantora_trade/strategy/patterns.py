"""Deterministic single- and two-candle pattern classification."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from quantora_trade.domain.models import Candle
from quantora_trade.strategy.validation import validate_closed_candle_series


class CandlestickPattern(StrEnum):
    DOJI = "doji"
    HAMMER = "hammer"
    SHOOTING_STAR = "shooting_star"
    BULLISH_ENGULFING = "bullish_engulfing"
    BEARISH_ENGULFING = "bearish_engulfing"


@dataclass(frozen=True, slots=True)
class PatternConfig:
    doji_body_ratio: Decimal = Decimal("0.10")
    wick_to_body_ratio: Decimal = Decimal("2")

    def __post_init__(self) -> None:
        if not Decimal("0") < self.doji_body_ratio < Decimal("1"):
            raise ValueError("doji_body_ratio must be between zero and one")
        if self.wick_to_body_ratio <= Decimal("1"):
            raise ValueError("wick_to_body_ratio must be greater than one")


@dataclass(frozen=True, slots=True)
class CandlePatternPoint:
    symbol: str
    timeframe: str
    observed_at: datetime
    patterns: tuple[CandlestickPattern, ...]


def _patterns_for(
    candle: Candle,
    previous: Candle | None,
    config: PatternConfig,
) -> tuple[CandlestickPattern, ...]:
    candle_range = candle.high - candle.low
    body = abs(candle.close - candle.open)
    upper_wick = candle.high - max(candle.open, candle.close)
    lower_wick = min(candle.open, candle.close) - candle.low
    detected: list[CandlestickPattern] = []
    if body <= candle_range * config.doji_body_ratio:
        detected.append(CandlestickPattern.DOJI)
    comparison_body = max(body, candle_range * config.doji_body_ratio)
    if lower_wick >= comparison_body * config.wick_to_body_ratio and upper_wick <= comparison_body:
        detected.append(CandlestickPattern.HAMMER)
    if upper_wick >= comparison_body * config.wick_to_body_ratio and lower_wick <= comparison_body:
        detected.append(CandlestickPattern.SHOOTING_STAR)
    if previous is not None:
        previous_low = min(previous.open, previous.close)
        previous_high = max(previous.open, previous.close)
        current_low = min(candle.open, candle.close)
        current_high = max(candle.open, candle.close)
        if (
            previous.close < previous.open
            and candle.close > candle.open
            and current_low <= previous_low
            and current_high >= previous_high
        ):
            detected.append(CandlestickPattern.BULLISH_ENGULFING)
        if (
            previous.close > previous.open
            and candle.close < candle.open
            and current_low <= previous_low
            and current_high >= previous_high
        ):
            detected.append(CandlestickPattern.BEARISH_ENGULFING)
    return tuple(detected)


def detect_candlestick_patterns(
    candles: tuple[Candle, ...],
    config: PatternConfig | None = None,
) -> tuple[CandlePatternPoint, ...]:
    """Classify each closed candle using itself and at most its predecessor."""

    validate_closed_candle_series(candles)
    config = config or PatternConfig()
    return tuple(
        CandlePatternPoint(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            observed_at=candle.close_time,
            patterns=_patterns_for(candle, candles[index - 1] if index else None, config),
        )
        for index, candle in enumerate(candles)
    )
