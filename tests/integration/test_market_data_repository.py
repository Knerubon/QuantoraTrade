"""PostgreSQL integration tests for market-data persistence."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from quantora_trade.domain.enums import AssetClass
from quantora_trade.domain.models import Candle, Instrument
from quantora_trade.infrastructure.database.market_data_models import (
    BrokerModel,
    CandleModel,
    InstrumentModel,
    MarketDataIssueModel,
    RawMarketRateModel,
)
from quantora_trade.infrastructure.database.market_data_repository import (
    PostgresMarketDataRepository,
)
from quantora_trade.market_data.quality import (
    DataQualityIssue,
    DataQualityIssueCode,
    DataQualitySeverity,
)
from quantora_trade.market_data.storage import CandleRecord, MarketDataBatch, RawRateRecord

DATABASE_URL = os.getenv("QUANTORA_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("QUANTORA_DATABASE_URL is required for integration tests", allow_module_level=True)
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)

engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def clean_market_data_tables() -> None:
    with SessionFactory() as session, session.begin():
        session.execute(delete(MarketDataIssueModel))
        session.execute(delete(CandleModel))
        session.execute(delete(RawMarketRateModel))
        session.execute(delete(InstrumentModel))
        session.execute(delete(BrokerModel))


def instrument(*, tick_value: Decimal = Decimal("1")) -> Instrument:
    return Instrument(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        quote_currency="USD",
        digits=5,
        point=Decimal("0.00001"),
        tick_size=Decimal("0.00001"),
        tick_value=tick_value,
        contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
    )


def market_candle(open_time: datetime, close: Decimal = Decimal("1.1005")) -> Candle:
    return Candle(
        symbol="EURUSD",
        timeframe="M15",
        open_time=open_time,
        close_time=open_time + timedelta(minutes=15),
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=close,
        tick_volume=100,
        is_closed=True,
    )


def raw_rate(open_time: datetime) -> RawRateRecord:
    payload: dict[str, object] = {
        "time": int(open_time.timestamp()),
        "open": "1.1000",
        "high": "1.1010",
        "low": "1.0990",
        "close": "1.1005",
        "tick_volume": 100,
        "spread": 12,
        "real_volume": 0,
    }
    return RawRateRecord(
        timeframe="M15",
        open_time=open_time,
        source="mt5",
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=Decimal("1.1005"),
        tick_volume=100,
        spread_points=12,
        real_volume=0,
        payload=payload,
    )


def batch(
    *,
    specification: Instrument | None = None,
    close: Decimal = Decimal("1.1005"),
) -> MarketDataBatch:
    source_rate = raw_rate(NOW - timedelta(minutes=15))
    normalized = market_candle(source_rate.open_time, close)
    return MarketDataBatch(
        instrument=specification or instrument(),
        broker_code="mt5-demo",
        timeframe="M15",
        raw_rates=(source_rate,),
        candles=(
            CandleRecord(
                candle=normalized,
                source="mt5",
                spread_points=12,
                payload_hash=source_rate.payload_hash,
            ),
        ),
        quality_issues=(
            DataQualityIssue(
                code=DataQualityIssueCode.GAP_DETECTED,
                severity=DataQualitySeverity.WARNING,
                message="Test warning.",
                candle_open_time=normalized.open_time,
            ),
        ),
        ingested_at=NOW,
    )


def row_count(model: type[object]) -> int:
    with SessionFactory() as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def repository() -> PostgresMarketDataRepository:
    return PostgresMarketDataRepository(SessionFactory)


def test_store_persists_raw_normalized_and_quality_records() -> None:
    result = repository().store(batch())

    assert result.raw_rows == 1
    assert result.candle_rows == 1
    assert result.issue_rows == 1
    assert row_count(BrokerModel) == 1
    assert row_count(InstrumentModel) == 1
    assert row_count(RawMarketRateModel) == 1
    assert row_count(CandleModel) == 1
    assert row_count(MarketDataIssueModel) == 1


def test_store_when_same_batch_replayed_deduplicates_raw_and_upserts_candle() -> None:
    store = repository()

    first = store.store(batch())
    second = store.store(batch())

    assert first.raw_rows == 1
    assert second.raw_rows == 0
    assert row_count(RawMarketRateModel) == 1
    assert row_count(CandleModel) == 1


def test_store_when_candle_is_revised_updates_normalized_record() -> None:
    store = repository()
    store.store(batch())
    store.store(batch(close=Decimal("1.1007")))

    candles = store.latest_closed_candles(
        broker_code="mt5-demo",
        symbol="EURUSD",
        timeframe="M15",
        limit=10,
    )

    assert len(candles) == 1
    assert candles[0].close == Decimal("1.100700000000")


def test_store_when_specification_changes_updates_instrument() -> None:
    store = repository()
    store.store(batch())
    store.store(batch(specification=instrument(tick_value=Decimal("1.25"))))

    with SessionFactory() as session:
        stored = session.scalar(select(InstrumentModel))

    assert stored is not None
    assert stored.tick_value == Decimal("1.25000000")


def test_latest_closed_candles_returns_ascending_limited_window() -> None:
    store = repository()
    initial = batch()
    earlier_raw = raw_rate(NOW - timedelta(minutes=30))
    earlier_candle = market_candle(earlier_raw.open_time, Decimal("1.0995"))
    expanded = MarketDataBatch(
        instrument=initial.instrument,
        broker_code=initial.broker_code,
        timeframe=initial.timeframe,
        raw_rates=(earlier_raw, *initial.raw_rates),
        candles=(
            CandleRecord(earlier_candle, "mt5", 11, earlier_raw.payload_hash),
            *initial.candles,
        ),
        quality_issues=(),
        ingested_at=NOW,
    )
    store.store(expanded)

    candles = store.latest_closed_candles(
        broker_code="mt5-demo",
        symbol="EURUSD",
        timeframe="M15",
        limit=1,
    )

    assert len(candles) == 1
    assert candles[0].open_time == NOW - timedelta(minutes=15)


def test_latest_closed_candles_when_limit_is_invalid_rejects_request() -> None:
    with pytest.raises(ValueError, match="limit"):
        repository().latest_closed_candles(
            broker_code="mt5-demo",
            symbol="EURUSD",
            timeframe="M15",
            limit=0,
        )
