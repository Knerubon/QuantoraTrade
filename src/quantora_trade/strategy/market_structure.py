"""Causal swing and support/resistance zone detection."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from quantora_trade.domain.models import Candle
from quantora_trade.strategy.validation import validate_closed_candle_series


class StructureKind(StrEnum):
    SUPPORT = "support"
    RESISTANCE = "resistance"


@dataclass(frozen=True, slots=True)
class MarketStructureConfig:
    """Symmetric pivot confirmation and zone width settings."""

    left_bars: int = 2
    right_bars: int = 2
    zone_tolerance: Decimal = Decimal("0.001")

    def __post_init__(self) -> None:
        if self.left_bars <= 0 or self.right_bars <= 0:
            raise ValueError("pivot bars must be greater than zero")
        if not Decimal("0") < self.zone_tolerance < Decimal("1"):
            raise ValueError("zone_tolerance must be between zero and one")


@dataclass(frozen=True, slots=True)
class SwingPoint:
    kind: StructureKind
    price: Decimal
    occurred_at: datetime
    confirmed_at: datetime


@dataclass(frozen=True, slots=True)
class PriceZone:
    kind: StructureKind
    lower: Decimal
    upper: Decimal
    first_confirmed_at: datetime
    last_confirmed_at: datetime
    touches: int

    @property
    def midpoint(self) -> Decimal:
        return (self.lower + self.upper) / Decimal("2")


@dataclass(frozen=True, slots=True)
class MarketStructurePoint:
    symbol: str
    timeframe: str
    observed_at: datetime
    swings: tuple[SwingPoint, ...]
    zones: tuple[PriceZone, ...]


def _is_pivot_high(candles: tuple[Candle, ...], index: int, left: int, right: int) -> bool:
    candidate = candles[index].high
    neighbors = candles[index - left : index] + candles[index + 1 : index + right + 1]
    return all(candidate > candle.high for candle in neighbors)


def _is_pivot_low(candles: tuple[Candle, ...], index: int, left: int, right: int) -> bool:
    candidate = candles[index].low
    neighbors = candles[index - left : index] + candles[index + 1 : index + right + 1]
    return all(candidate < candle.low for candle in neighbors)


def _merge_zone(
    zones: list[PriceZone],
    swing: SwingPoint,
    tolerance: Decimal,
) -> None:
    width = abs(swing.price) * tolerance
    lower = swing.price - width
    upper = swing.price + width
    for index, zone in enumerate(zones):
        if zone.kind is swing.kind and lower <= zone.upper and upper >= zone.lower:
            zones[index] = replace(
                zone,
                lower=min(zone.lower, lower),
                upper=max(zone.upper, upper),
                last_confirmed_at=swing.confirmed_at,
                touches=zone.touches + 1,
            )
            return
    zones.append(
        PriceZone(
            kind=swing.kind,
            lower=lower,
            upper=upper,
            first_confirmed_at=swing.confirmed_at,
            last_confirmed_at=swing.confirmed_at,
            touches=1,
        )
    )


def calculate_market_structure(
    candles: tuple[Candle, ...],
    config: MarketStructureConfig | None = None,
) -> tuple[MarketStructurePoint, ...]:
    """Confirm pivots only after right-side bars close, avoiding future leakage."""

    validate_closed_candle_series(candles)
    config = config or MarketStructureConfig()
    swings: list[SwingPoint] = []
    zones: list[PriceZone] = []
    result: list[MarketStructurePoint] = []
    for observed_index, candle in enumerate(candles):
        pivot_index = observed_index - config.right_bars
        if pivot_index >= config.left_bars:
            pivot = candles[pivot_index]
            if _is_pivot_low(candles, pivot_index, config.left_bars, config.right_bars):
                swing = SwingPoint(
                    kind=StructureKind.SUPPORT,
                    price=pivot.low,
                    occurred_at=pivot.close_time,
                    confirmed_at=candle.close_time,
                )
                swings.append(swing)
                _merge_zone(zones, swing, config.zone_tolerance)
            if _is_pivot_high(candles, pivot_index, config.left_bars, config.right_bars):
                swing = SwingPoint(
                    kind=StructureKind.RESISTANCE,
                    price=pivot.high,
                    occurred_at=pivot.close_time,
                    confirmed_at=candle.close_time,
                )
                swings.append(swing)
                _merge_zone(zones, swing, config.zone_tolerance)
        result.append(
            MarketStructurePoint(
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                observed_at=candle.close_time,
                swings=tuple(swings),
                zones=tuple(zones),
            )
        )
    return tuple(result)
