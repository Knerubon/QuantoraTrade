"""Immutable simulation clock with causal multi-symbol candle ordering."""

from dataclasses import dataclass
from datetime import datetime

from quantora_trade.domain.models import Candle
from quantora_trade.market_data.timeframes import Timeframe


@dataclass(frozen=True, slots=True)
class CandleEvent:
    """One closed candle becoming observable by the simulation."""

    candle: Candle

    @property
    def occurred_at(self) -> datetime:
        return self.candle.close_time


@dataclass(frozen=True, slots=True)
class SimulationClock:
    """Persistent cursor over a prevalidated deterministic event sequence."""

    events: tuple[CandleEvent, ...]
    cursor: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.cursor <= len(self.events):
            raise ValueError("simulation cursor is outside the event sequence")

    @classmethod
    def from_candles(cls, candles: tuple[Candle, ...]) -> "SimulationClock":
        """Validate and order closed candles by time, context priority, and identity."""

        if not candles:
            raise ValueError("simulation requires at least one candle")
        identities: set[tuple[str, str, datetime]] = set()
        sortable: list[tuple[datetime, float, str, str, datetime, Candle]] = []
        for candle in candles:
            if not candle.is_closed:
                raise ValueError("simulation clock accepts closed candles only")
            if candle.symbol != candle.symbol.strip().upper():
                raise ValueError("simulation symbol must be canonical uppercase")
            try:
                timeframe = Timeframe(candle.timeframe)
            except ValueError as error:
                raise ValueError(f"unsupported simulation timeframe: {candle.timeframe}") from error
            if candle.close_time - candle.open_time != timeframe.duration:
                raise ValueError("candle duration does not match its timeframe")
            identity = (candle.symbol, candle.timeframe, candle.open_time)
            if identity in identities:
                raise ValueError("simulation contains a duplicate candle")
            identities.add(identity)
            sortable.append(
                (
                    candle.close_time,
                    -timeframe.duration.total_seconds(),
                    candle.symbol,
                    candle.timeframe,
                    candle.open_time,
                    candle,
                )
            )
        ordered = tuple(
            CandleEvent(item[-1]) for item in sorted(sortable, key=lambda item: item[:-1])
        )
        return cls(events=ordered)

    @property
    def is_finished(self) -> bool:
        return self.cursor == len(self.events)

    @property
    def remaining(self) -> int:
        return len(self.events) - self.cursor

    def peek(self) -> CandleEvent:
        if self.is_finished:
            raise StopIteration("simulation clock is exhausted")
        return self.events[self.cursor]

    def advance(self) -> tuple[CandleEvent, "SimulationClock"]:
        """Return the next event and a new clock without mutating prior state."""

        event = self.peek()
        return event, SimulationClock(events=self.events, cursor=self.cursor + 1)
