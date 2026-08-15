"""PostgreSQL repository for atomic market-data persistence."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quantora_trade.domain.models import Candle, Instrument
from quantora_trade.infrastructure.database.market_data_models import (
    BrokerModel,
    CandleModel,
    InstrumentModel,
    MarketDataIssueModel,
    RawMarketRateModel,
)
from quantora_trade.market_data.storage import (
    MarketDataBatch,
    MarketDataStorePort,
    StoredMarketData,
)


def _instrument_hash(instrument: Instrument) -> str:
    payload = {
        "symbol": instrument.symbol,
        "asset_class": instrument.asset_class,
        "quote_currency": instrument.quote_currency,
        "digits": instrument.digits,
        "point": str(instrument.point),
        "tick_size": str(instrument.tick_size),
        "tick_value": str(instrument.tick_value),
        "contract_size": str(instrument.contract_size),
        "volume_min": str(instrument.volume_min),
        "volume_max": str(instrument.volume_max),
        "volume_step": str(instrument.volume_step),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PostgresMarketDataRepository(MarketDataStorePort):
    """Stores raw rates, normalized candles, and issues in one transaction."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def store(self, batch: MarketDataBatch) -> StoredMarketData:
        with self._session_factory() as session, session.begin():
            broker_id = self._upsert_broker(session, batch)
            instrument_id = self._upsert_instrument(session, broker_id, batch)
            raw_rows = self._store_raw_rates(session, instrument_id, batch)
            candle_rows = self._store_candles(session, instrument_id, batch)
            issue_rows = self._store_issues(session, instrument_id, batch)
            return StoredMarketData(
                instrument_id=str(instrument_id),
                raw_rows=raw_rows,
                candle_rows=candle_rows,
                issue_rows=issue_rows,
            )

    def latest_closed_candles(
        self,
        *,
        broker_code: str,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> tuple[Candle, ...]:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        statement: Select[tuple[CandleModel]] = (
            select(CandleModel)
            .join(InstrumentModel, CandleModel.instrument_id == InstrumentModel.id)
            .join(BrokerModel, InstrumentModel.broker_id == BrokerModel.id)
            .where(
                BrokerModel.code == broker_code,
                InstrumentModel.symbol == symbol,
                CandleModel.timeframe == timeframe,
                CandleModel.is_closed.is_(True),
            )
            .order_by(CandleModel.open_time.desc())
            .limit(limit)
        )
        with self._session_factory() as session:
            rows = tuple(reversed(session.scalars(statement).all()))
        return tuple(
            Candle(
                symbol=symbol,
                timeframe=row.timeframe,
                open_time=row.open_time.astimezone(UTC),
                close_time=row.close_time.astimezone(UTC),
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                tick_volume=row.tick_volume,
                is_closed=row.is_closed,
            )
            for row in rows
        )

    @staticmethod
    def _upsert_broker(session: Session, batch: MarketDataBatch) -> UUID:
        statement = (
            insert(BrokerModel)
            .values(
                code=batch.broker_code,
                name=batch.broker_code,
                adapter_type="mt5",
                enabled=True,
                created_at=batch.ingested_at,
            )
            .on_conflict_do_update(
                index_elements=[BrokerModel.code],
                set_={"enabled": True},
            )
            .returning(BrokerModel.id)
        )
        return session.execute(statement).scalar_one()

    @staticmethod
    def _upsert_instrument(session: Session, broker_id: UUID, batch: MarketDataBatch) -> UUID:
        instrument = batch.instrument
        values = {
            "broker_id": broker_id,
            "symbol": instrument.symbol,
            "canonical_symbol": instrument.symbol,
            "asset_class": instrument.asset_class.value,
            "quote_currency": instrument.quote_currency,
            "digits": instrument.digits,
            "point": instrument.point,
            "tick_size": instrument.tick_size,
            "tick_value": instrument.tick_value,
            "contract_size": instrument.contract_size,
            "volume_min": instrument.volume_min,
            "volume_max": instrument.volume_max,
            "volume_step": instrument.volume_step,
            "specification_hash": _instrument_hash(instrument),
            "observed_at": batch.ingested_at,
            "created_at": batch.ingested_at,
            "updated_at": batch.ingested_at,
        }
        statement = (
            insert(InstrumentModel)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_instruments_broker_symbol",
                set_={key: value for key, value in values.items() if key not in {"created_at"}},
            )
            .returning(InstrumentModel.id)
        )
        return session.execute(statement).scalar_one()

    @staticmethod
    def _store_raw_rates(session: Session, instrument_id: UUID, batch: MarketDataBatch) -> int:
        inserted = 0
        for rate in batch.raw_rates:
            statement = (
                insert(RawMarketRateModel)
                .values(
                    instrument_id=instrument_id,
                    timeframe=rate.timeframe,
                    open_time=rate.open_time,
                    source=rate.source,
                    open=rate.open,
                    high=rate.high,
                    low=rate.low,
                    close=rate.close,
                    tick_volume=rate.tick_volume,
                    spread_points=rate.spread_points,
                    real_volume=rate.real_volume,
                    payload=rate.payload,
                    payload_hash=rate.payload_hash,
                    ingested_at=batch.ingested_at,
                )
                .on_conflict_do_nothing(constraint="uq_raw_rate_lineage")
                .returning(RawMarketRateModel.id)
            )
            if session.execute(statement).scalar_one_or_none() is not None:
                inserted += 1
        return inserted

    @staticmethod
    def _store_candles(session: Session, instrument_id: UUID, batch: MarketDataBatch) -> int:
        affected = 0
        for record in batch.candles:
            candle = record.candle
            statement = (
                insert(CandleModel)
                .values(
                    instrument_id=instrument_id,
                    timeframe=candle.timeframe,
                    open_time=candle.open_time,
                    close_time=candle.close_time,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    tick_volume=candle.tick_volume,
                    spread_points=record.spread_points,
                    source=record.source,
                    is_closed=candle.is_closed,
                    payload_hash=record.payload_hash,
                    ingested_at=batch.ingested_at,
                )
                .on_conflict_do_update(
                    constraint="uq_candle_source_time",
                    set_={
                        "close_time": candle.close_time,
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "tick_volume": candle.tick_volume,
                        "spread_points": record.spread_points,
                        "is_closed": candle.is_closed,
                        "payload_hash": record.payload_hash,
                        "ingested_at": batch.ingested_at,
                    },
                )
                .returning(CandleModel.id)
            )
            session.execute(statement).scalar_one()
            affected += 1
        return affected

    @staticmethod
    def _store_issues(session: Session, instrument_id: UUID, batch: MarketDataBatch) -> int:
        for issue in batch.quality_issues:
            session.add(
                MarketDataIssueModel(
                    instrument_id=instrument_id,
                    timeframe=batch.timeframe,
                    issue_code=issue.code.value,
                    severity=issue.severity.value,
                    message=issue.message,
                    candle_open_time=issue.candle_open_time,
                    detected_at=batch.ingested_at,
                )
            )
        return len(batch.quality_issues)


__all__ = ["PostgresMarketDataRepository"]
