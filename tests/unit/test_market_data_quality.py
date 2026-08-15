"""Tests for deterministic market-data validation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantora_trade.domain.models import Candle
from quantora_trade.market_data.quality import (
    DataQualityIssueCode,
    MarketDataValidator,
)

NOW = datetime(2026, 8, 15, 12, 30, tzinfo=UTC)
INTERVAL = timedelta(minutes=15)


def candle(
    open_time: datetime,
    *,
    symbol: str = "EURUSD",
    timeframe: str = "M15",
    is_closed: bool = True,
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + INTERVAL,
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=Decimal("1.1005"),
        tick_volume=100,
        is_closed=is_closed,
    )


def validator() -> MarketDataValidator:
    return MarketDataValidator(
        expected_interval=INTERVAL,
        max_staleness=timedelta(minutes=20),
    )


def issue_codes(report: object) -> set[DataQualityIssueCode]:
    return {issue.code for issue in report.issues}  # type: ignore[attr-defined]


def test_validator_when_candles_are_valid_marks_data_usable() -> None:
    candles = (
        candle(NOW - timedelta(minutes=30)),
        candle(NOW - timedelta(minutes=15)),
    )

    report = validator().validate(candles, symbol="EURUSD", timeframe="M15", as_of=NOW)

    assert report.is_usable is True
    assert report.candle_count == 2
    assert report.issues == ()


def test_validator_when_empty_blocks_analysis() -> None:
    report = validator().validate((), symbol="EURUSD", timeframe="M15", as_of=NOW)

    assert report.is_usable is False
    assert issue_codes(report) == {DataQualityIssueCode.EMPTY_DATA}


def test_validator_when_gap_exists_warns_without_blocking() -> None:
    candles = (
        candle(NOW - timedelta(hours=1)),
        candle(NOW - timedelta(minutes=15)),
    )

    report = validator().validate(candles, symbol="EURUSD", timeframe="M15", as_of=NOW)

    assert report.is_usable is True
    assert DataQualityIssueCode.GAP_DETECTED in issue_codes(report)


def test_validator_when_latest_candle_is_stale_blocks_analysis() -> None:
    candles = (candle(NOW - timedelta(hours=1)),)

    report = validator().validate(candles, symbol="EURUSD", timeframe="M15", as_of=NOW)

    assert report.is_usable is False
    assert DataQualityIssueCode.STALE_DATA in issue_codes(report)


def test_validator_when_identity_or_state_is_invalid_reports_all_errors() -> None:
    open_time = NOW - timedelta(minutes=15)
    candles = (
        candle(open_time, symbol="GBPUSD", timeframe="M5", is_closed=False),
        candle(open_time),
    )

    report = validator().validate(candles, symbol="EURUSD", timeframe="M15", as_of=NOW)

    assert report.is_usable is False
    assert {
        DataQualityIssueCode.SYMBOL_MISMATCH,
        DataQualityIssueCode.TIMEFRAME_MISMATCH,
        DataQualityIssueCode.FORMING_CANDLE,
        DataQualityIssueCode.DUPLICATE_CANDLE,
    }.issubset(issue_codes(report))


def test_validator_when_candles_are_out_of_order_blocks_analysis() -> None:
    candles = (
        candle(NOW - timedelta(minutes=15)),
        candle(NOW - timedelta(minutes=30)),
    )

    report = validator().validate(candles, symbol="EURUSD", timeframe="M15", as_of=NOW)

    assert report.is_usable is False
    assert DataQualityIssueCode.OUT_OF_ORDER in issue_codes(report)
