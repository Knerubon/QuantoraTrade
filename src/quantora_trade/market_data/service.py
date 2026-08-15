"""Application-facing market-data orchestration."""

from dataclasses import dataclass
from datetime import datetime

from quantora_trade.domain.models import Candle, Instrument
from quantora_trade.domain.ports import MarketDataPort
from quantora_trade.market_data.errors import DataQualityError
from quantora_trade.market_data.quality import DataQualityReport, MarketDataValidator


@dataclass(frozen=True, slots=True)
class MarketDataSnapshot:
    """Validated candles plus the specification and quality evidence."""

    instrument: Instrument
    timeframe: str
    candles: tuple[Candle, ...]
    quality: DataQualityReport
    as_of: datetime


@dataclass(frozen=True, slots=True)
class MarketDataService:
    """Loads closed candles and refuses unsafe data."""

    source: MarketDataPort

    def load_validated_snapshot(
        self,
        *,
        symbol: str,
        timeframe: str,
        until: datetime,
        limit: int,
        validator: MarketDataValidator,
    ) -> MarketDataSnapshot:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        instrument = self.source.instrument(symbol)
        candles = tuple(
            self.source.closed_candles(
                symbol,
                timeframe,
                until=until,
                limit=limit,
            )
        )
        report = validator.validate(
            candles,
            symbol=symbol,
            timeframe=timeframe,
            as_of=until,
        )
        if not report.is_usable:
            codes = ",".join(issue.code for issue in report.issues)
            raise DataQualityError(f"Market data blocked by: {codes}")

        return MarketDataSnapshot(
            instrument=instrument,
            timeframe=timeframe,
            candles=candles,
            quality=report,
            as_of=until,
        )
