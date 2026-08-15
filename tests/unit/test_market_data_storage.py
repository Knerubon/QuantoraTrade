"""Tests for storage contracts and source lineage."""

from datetime import UTC, datetime
from decimal import Decimal

from quantora_trade.market_data.storage import RawRateRecord


def test_raw_rate_payload_hash_is_stable_across_key_order() -> None:
    common = {
        "timeframe": "M15",
        "open_time": datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        "source": "mt5",
        "open": Decimal("1.1"),
        "high": Decimal("1.2"),
        "low": Decimal("1.0"),
        "close": Decimal("1.15"),
        "tick_volume": 100,
        "spread_points": 10,
        "real_volume": 0,
    }
    first = RawRateRecord(payload={"time": 1, "close": "1.15"}, **common)
    second = RawRateRecord(payload={"close": "1.15", "time": 1}, **common)

    assert first.payload_hash == second.payload_hash
    assert len(first.payload_hash) == 64
