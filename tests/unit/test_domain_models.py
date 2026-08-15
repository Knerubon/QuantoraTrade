"""Unit tests for immutable domain invariants."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from quantora_trade.domain.enums import Action, AssetClass, TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent, Candle, Instrument, RiskAssessment


def test_instrument_when_volume_range_is_invalid_rejects_specification() -> None:
    with pytest.raises(ValueError, match="volume_min"):
        Instrument(
            symbol="XAUUSD",
            asset_class=AssetClass.METAL,
            quote_currency="USD",
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.01"),
            tick_value=Decimal("1.00"),
            contract_size=Decimal("100"),
            volume_min=Decimal("2"),
            volume_max=Decimal("1"),
            volume_step=Decimal("0.01"),
        )


def test_candle_when_datetime_is_naive_rejects_value() -> None:
    start = datetime(2026, 8, 15, 12, 0)
    with pytest.raises(ValueError, match="UTC"):
        Candle(
            symbol="EURUSD",
            timeframe="M15",
            open_time=start,
            close_time=start + timedelta(minutes=15),
            open=Decimal("1.1000"),
            high=Decimal("1.1010"),
            low=Decimal("1.0990"),
            close=Decimal("1.1005"),
            tick_volume=100,
            is_closed=True,
        )


def test_candle_when_ohlc_is_invalid_rejects_value() -> None:
    start = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="high"):
        Candle(
            symbol="EURUSD",
            timeframe="M15",
            open_time=start,
            close_time=start + timedelta(minutes=15),
            open=Decimal("1.1000"),
            high=Decimal("1.0990"),
            low=Decimal("1.0980"),
            close=Decimal("1.1005"),
            tick_volume=100,
            is_closed=True,
        )


def test_risk_assessment_when_rejected_without_reason_rejects_value() -> None:
    with pytest.raises(ValueError, match="rejection codes"):
        RiskAssessment(
            id=uuid4(),
            decision_id=uuid4(),
            policy_version="risk-v1",
            approved=False,
            rejection_codes=(),
            risk_amount=Decimal("0"),
            volume=Decimal("0"),
            stop_loss=None,
            take_profit=None,
            created_at=datetime.now(UTC),
        )


def test_order_intent_when_side_is_hold_rejects_value() -> None:
    with pytest.raises(ValueError, match="HOLD"):
        ApprovedOrderIntent(
            id=uuid4(),
            risk_assessment_id=uuid4(),
            idempotency_key="idem-1",
            mode=TradingMode.PAPER,
            symbol="USDJPY",
            side=Action.HOLD,
            volume=Decimal("0.01"),
            stop_loss=Decimal("147.000"),
            take_profit=Decimal("148.000"),
            created_at=datetime.now(UTC),
        )
