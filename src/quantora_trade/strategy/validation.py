"""Shared validation for causal strategy calculations."""

from datetime import datetime

from quantora_trade.domain.models import Candle


def validate_closed_candle_series(candles: tuple[Candle, ...]) -> None:
    """Require one strictly ordered closed-candle identity."""

    if not candles:
        raise ValueError("at least one candle is required")
    identity = (candles[0].symbol, candles[0].timeframe)
    previous_open: datetime | None = None
    for candle in candles:
        if not candle.is_closed:
            raise ValueError("strategy calculations require closed candles")
        if (candle.symbol, candle.timeframe) != identity:
            raise ValueError("candles must share symbol and timeframe")
        if previous_open is not None and candle.open_time <= previous_open:
            raise ValueError("candles must be strictly ordered without duplicates")
        previous_open = candle.open_time
