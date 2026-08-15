"""Persistence contracts for raw and normalized market data."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from quantora_trade.domain.models import Candle, Instrument
from quantora_trade.market_data.quality import DataQualityIssue


@dataclass(frozen=True, slots=True)
class RawRateRecord:
    """Source-preserving market rate before domain normalization."""

    timeframe: str
    open_time: datetime
    source: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tick_volume: int
    spread_points: int
    real_volume: int
    payload: dict[str, object]

    @property
    def payload_hash(self) -> str:
        canonical = json.dumps(
            self.payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CandleRecord:
    """Normalized candle plus source lineage."""

    candle: Candle
    source: str
    spread_points: int | None
    payload_hash: str


@dataclass(frozen=True, slots=True)
class MarketDataBatch:
    """Atomic unit persisted for one instrument and timeframe."""

    instrument: Instrument
    broker_code: str
    timeframe: str
    raw_rates: tuple[RawRateRecord, ...]
    candles: tuple[CandleRecord, ...]
    quality_issues: tuple[DataQualityIssue, ...]
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class StoredMarketData:
    """Counts returned after an idempotent storage transaction."""

    instrument_id: str
    raw_rows: int
    candle_rows: int
    issue_rows: int


class MarketDataStorePort(Protocol):
    """Persists one market-data batch atomically."""

    def store(self, batch: MarketDataBatch) -> StoredMarketData: ...

    def latest_closed_candles(
        self,
        *,
        broker_code: str,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> tuple[Candle, ...]: ...
