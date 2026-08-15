"""Canonical timeframe definitions independent from broker SDK constants."""

from datetime import timedelta
from enum import StrEnum


class Timeframe(StrEnum):
    """Timeframes supported by the initial market-data layer."""

    M5 = "M5"
    M15 = "M15"
    H1 = "H1"

    @property
    def duration(self) -> timedelta:
        durations = {
            Timeframe.M5: timedelta(minutes=5),
            Timeframe.M15: timedelta(minutes=15),
            Timeframe.H1: timedelta(hours=1),
        }
        return durations[self]
