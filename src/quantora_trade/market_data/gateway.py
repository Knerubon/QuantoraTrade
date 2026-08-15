"""Typed gateway contracts isolating the MetaTrader 5 SDK."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MT5SymbolInfo:
    """Subset of broker symbol metadata required by the domain."""

    symbol: str
    path: str
    currency_base: str | None
    currency_profit: str
    digits: int
    point: Decimal
    spread_points: int
    trade_tick_size: Decimal
    trade_tick_value: Decimal
    trade_contract_size: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal


@dataclass(frozen=True, slots=True)
class MT5Rate:
    """Normalized MT5 rate returned by a gateway."""

    epoch_seconds: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tick_volume: int
    spread_points: int
    real_volume: int


class MT5Gateway(Protocol):
    """Narrow interface implemented by the real SDK wrapper or a test fake."""

    def initialize(self) -> None: ...

    def shutdown(self) -> None: ...

    def symbol_info(self, symbol: str) -> MT5SymbolInfo | None: ...

    def copy_rates_range(
        self,
        symbol: str,
        timeframe: str,
        date_from: datetime,
        date_to: datetime,
    ) -> Sequence[MT5Rate]: ...
