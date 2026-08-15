"""MetaTrader 5 adapter with lazy optional SDK loading."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from types import ModuleType
from typing import Any, cast

from quantora_trade.domain.enums import AssetClass
from quantora_trade.domain.models import Candle, Instrument
from quantora_trade.market_data.errors import (
    MT5ConnectionError,
    MT5UnavailableError,
    SymbolNotFoundError,
    UnsupportedTimeframeError,
)
from quantora_trade.market_data.gateway import MT5Gateway, MT5Rate, MT5SymbolInfo
from quantora_trade.market_data.timeframes import Timeframe

TIMEFRAME_CONSTANTS = {
    Timeframe.M5.value: "TIMEFRAME_M5",
    Timeframe.M15.value: "TIMEFRAME_M15",
    Timeframe.H1.value: "TIMEFRAME_H1",
}


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


class MetaTrader5Gateway:
    """Thin wrapper around the optional MetaTrader5 Python package."""

    def __init__(self, module: ModuleType | None = None) -> None:
        self._module = module

    @property
    def module(self) -> ModuleType:
        if self._module is None:
            try:
                self._module = import_module("MetaTrader5")
            except ImportError as exc:
                raise MT5UnavailableError(
                    "MetaTrader5 is optional and must be installed on the Windows MT5 host."
                ) from exc
        return self._module

    def initialize(self) -> None:
        initialize = cast(Any, self.module).initialize
        if not bool(initialize()):
            last_error = cast(Any, self.module).last_error()
            raise MT5ConnectionError(f"MT5 initialize failed: {last_error!r}")

    def shutdown(self) -> None:
        shutdown = cast(Any, self.module).shutdown
        shutdown()

    def symbol_info(self, symbol: str) -> MT5SymbolInfo | None:
        get_symbol_info = cast(Any, self.module).symbol_info
        info = get_symbol_info(symbol)
        if info is None:
            return None
        return MT5SymbolInfo(
            symbol=str(info.name),
            path=str(info.path),
            currency_base=str(info.currency_base) if info.currency_base else None,
            currency_profit=str(info.currency_profit),
            digits=int(info.digits),
            point=_decimal(info.point),
            trade_tick_size=_decimal(info.trade_tick_size),
            trade_tick_value=_decimal(info.trade_tick_value),
            trade_contract_size=_decimal(info.trade_contract_size),
            volume_min=_decimal(info.volume_min),
            volume_max=_decimal(info.volume_max),
            volume_step=_decimal(info.volume_step),
        )

    def copy_rates_range(
        self,
        symbol: str,
        timeframe: str,
        date_from: datetime,
        date_to: datetime,
    ) -> Sequence[MT5Rate]:
        constant_name = TIMEFRAME_CONSTANTS.get(timeframe)
        if constant_name is None:
            raise UnsupportedTimeframeError(f"Unsupported timeframe: {timeframe}")
        timeframe_constant = getattr(self.module, constant_name)
        copy_rates = cast(Any, self.module).copy_rates_range
        rows = copy_rates(symbol, timeframe_constant, date_from, date_to)
        if rows is None:
            last_error = cast(Any, getattr(self.module, "last_error"))()
            raise MT5ConnectionError(f"MT5 copy_rates_range failed: {last_error!r}")
        return tuple(
            MT5Rate(
                epoch_seconds=int(row["time"]),
                open=_decimal(row["open"]),
                high=_decimal(row["high"]),
                low=_decimal(row["low"]),
                close=_decimal(row["close"]),
                tick_volume=int(row["tick_volume"]),
                spread_points=int(row["spread"]),
                real_volume=int(row["real_volume"]),
            )
            for row in rows
        )


class MT5MarketDataAdapter:
    """Normalizes broker-specific MT5 data into domain contracts."""

    def __init__(self, gateway: MT5Gateway) -> None:
        self._gateway = gateway

    def instrument(self, symbol: str) -> Instrument:
        info = self._gateway.symbol_info(symbol)
        if info is None:
            raise SymbolNotFoundError(f"MT5 symbol was not found: {symbol}")
        asset_class = (
            AssetClass.FOREX if info.currency_base and info.currency_profit else AssetClass.METAL
        )
        return Instrument(
            symbol=info.symbol,
            asset_class=asset_class,
            quote_currency=info.currency_profit,
            digits=info.digits,
            point=info.point,
            tick_size=info.trade_tick_size,
            tick_value=info.trade_tick_value,
            contract_size=info.trade_contract_size,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
        )

    def closed_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        until: datetime,
        limit: int,
    ) -> Sequence[Candle]:
        if until.tzinfo is None or until.utcoffset() != UTC.utcoffset(until):
            raise ValueError("until must be timezone-aware UTC")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        try:
            duration = Timeframe(timeframe).duration
        except ValueError as exc:
            raise UnsupportedTimeframeError(f"Unsupported timeframe: {timeframe}") from exc

        date_from = until - (duration * (limit + 10))
        rates = self._gateway.copy_rates_range(symbol, timeframe, date_from, until)
        candles = tuple(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=datetime.fromtimestamp(rate.epoch_seconds, tz=UTC),
                close_time=datetime.fromtimestamp(rate.epoch_seconds, tz=UTC) + duration,
                open=rate.open,
                high=rate.high,
                low=rate.low,
                close=rate.close,
                tick_volume=rate.tick_volume,
                is_closed=datetime.fromtimestamp(rate.epoch_seconds, tz=UTC) + duration <= until,
            )
            for rate in rates
        )
        closed = tuple(candle for candle in candles if candle.is_closed)
        return closed[-limit:]
