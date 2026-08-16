"""Unit tests for immutable domain invariants."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from quantora_trade.domain.enums import Action, AssetClass, SignalReasonCode, TradingMode
from quantora_trade.domain.models import (
    ApprovedOrderIntent,
    Candle,
    Decision,
    Instrument,
    RiskAssessment,
    Signal,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def make_instrument(**overrides: object) -> Instrument:
    values: dict[str, object] = {
        "symbol": "XAUUSD",
        "asset_class": AssetClass.METAL,
        "quote_currency": "USD",
        "digits": 2,
        "point": Decimal("0.01"),
        "pip_size": Decimal("0.01"),
        "tick_size": Decimal("0.01"),
        "tick_value": Decimal("1.00"),
        "contract_size": Decimal("100"),
        "spread_points": 25,
        "session_timezone": "UTC",
        "session_profile": "metals_24x5",
        "volume_min": Decimal("0.01"),
        "volume_max": Decimal("100"),
        "volume_step": Decimal("0.01"),
    }
    values.update(overrides)
    return Instrument(**values)  # type: ignore[arg-type]


def test_instrument_when_valid_accepts_broker_specification() -> None:
    instrument = make_instrument()

    assert instrument.symbol == "XAUUSD"
    assert instrument.tick_size == Decimal("0.01")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("point", Decimal("0")),
        ("pip_size", Decimal("0")),
        ("tick_size", Decimal("-0.01")),
        ("tick_value", Decimal("0")),
        ("contract_size", Decimal("0")),
        ("volume_min", Decimal("0")),
        ("volume_max", Decimal("0")),
        ("volume_step", Decimal("0")),
    ],
)
def test_instrument_when_positive_field_is_invalid_rejects_specification(
    field_name: str,
    value: Decimal,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_instrument(**{field_name: value})


def test_instrument_when_symbol_is_blank_rejects_specification() -> None:
    with pytest.raises(ValueError, match="symbol"):
        make_instrument(symbol=" ")


def test_instrument_when_digits_are_negative_rejects_specification() -> None:
    with pytest.raises(ValueError, match="digits"):
        make_instrument(digits=-1)


def test_instrument_when_volume_range_is_invalid_rejects_specification() -> None:
    with pytest.raises(ValueError, match="volume_min"):
        make_instrument(volume_min=Decimal("2"), volume_max=Decimal("1"))


def make_candle(**overrides: object) -> Candle:
    values: dict[str, object] = {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "open_time": NOW,
        "close_time": NOW + timedelta(minutes=15),
        "open": Decimal("1.1000"),
        "high": Decimal("1.1010"),
        "low": Decimal("1.0990"),
        "close": Decimal("1.1005"),
        "tick_volume": 100,
        "is_closed": True,
    }
    values.update(overrides)
    return Candle(**values)  # type: ignore[arg-type]


def test_candle_when_valid_accepts_closed_bar() -> None:
    candle = make_candle()

    assert candle.is_closed is True
    assert candle.close == Decimal("1.1005")


def test_candle_when_datetime_is_naive_rejects_value() -> None:
    naive_start = datetime(2026, 8, 15, 12, 0)
    with pytest.raises(ValueError, match="UTC"):
        make_candle(
            open_time=naive_start,
            close_time=naive_start + timedelta(minutes=15),
        )


def test_candle_when_close_time_is_not_after_open_rejects_value() -> None:
    with pytest.raises(ValueError, match="close_time"):
        make_candle(close_time=NOW)


def test_candle_when_high_is_invalid_rejects_value() -> None:
    with pytest.raises(ValueError, match="high"):
        make_candle(high=Decimal("1.0990"))


def test_candle_when_low_is_invalid_rejects_value() -> None:
    with pytest.raises(ValueError, match="low"):
        make_candle(low=Decimal("1.1007"))


def test_candle_when_volume_is_negative_rejects_value() -> None:
    with pytest.raises(ValueError, match="tick_volume"):
        make_candle(tick_volume=-1)


def make_signal(**overrides: object) -> Signal:
    values: dict[str, object] = {
        "id": uuid4(),
        "symbol": "EURUSD",
        "timeframe": "M15",
        "action": Action.BUY,
        "confidence": Decimal("0.75"),
        "strategy_version": "trend-pullback@1.0.0",
        "reason_codes": ("H1_BULLISH_CONTEXT",),
        "observed_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
    }
    values.update(overrides)
    return Signal(**values)  # type: ignore[arg-type]


def test_signal_when_valid_is_created() -> None:
    signal = make_signal()

    assert signal.action is Action.BUY


def test_signal_when_expired_at_observation_rejects_value() -> None:
    with pytest.raises(ValueError, match="expires_at"):
        make_signal(expires_at=NOW)


@pytest.mark.parametrize("confidence", [Decimal("-0.01"), Decimal("1.01")])
def test_signal_when_confidence_is_out_of_range_rejects_value(confidence: Decimal) -> None:
    with pytest.raises(ValueError, match="confidence"):
        make_signal(confidence=confidence)


@pytest.mark.parametrize("confidence", [Decimal("NaN"), Decimal("Infinity")])
def test_signal_when_confidence_is_not_finite_rejects_value(confidence: Decimal) -> None:
    with pytest.raises(ValueError, match="finite Decimal"):
        make_signal(confidence=confidence)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"symbol": "xauusd"}, "canonical uppercase"),
        ({"timeframe": "D1"}, "not supported"),
        ({"action": "NOT_AN_ACTION"}, "BUY, SELL, or HOLD"),
        ({"reason_codes": ("MADE_UP_REASON",)}, "unknown reason code"),
        (
            {
                "action": Action.BUY,
                "reason_codes": (SignalReasonCode.EMA_BEARISH_ALIGNMENT.value,),
            },
            "incompatible",
        ),
        (
            {
                "action": Action.HOLD,
                "reason_codes": (SignalReasonCode.EMA_BULLISH_ALIGNMENT.value,),
            },
            "directional",
        ),
    ],
)
def test_signal_rejects_noncanonical_or_semantically_invalid_values(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        make_signal(**overrides)


def test_decision_when_valid_is_created() -> None:
    decision = Decision(
        id=uuid4(),
        signal_id=uuid4(),
        symbol="GBPUSD",
        timeframe="M15",
        action=Action.SELL,
        confidence=Decimal("0.70"),
        policy_version="decision-v1",
        reason_codes=("H1_BEARISH_CONTEXT",),
        expires_at=NOW + timedelta(minutes=15),
    )

    assert decision.action is Action.SELL


def test_decision_when_confidence_is_invalid_rejects_value() -> None:
    with pytest.raises(ValueError, match="confidence"):
        Decision(
            id=uuid4(),
            signal_id=uuid4(),
            symbol="GBPUSD",
            timeframe="M15",
            action=Action.SELL,
            confidence=Decimal("2"),
            policy_version="decision-v1",
            reason_codes=(),
            expires_at=NOW + timedelta(minutes=15),
        )


def make_risk_assessment(**overrides: object) -> RiskAssessment:
    values: dict[str, object] = {
        "id": uuid4(),
        "decision_id": uuid4(),
        "policy_version": "risk-v1",
        "approved": True,
        "rejection_codes": (),
        "risk_amount": Decimal("25"),
        "volume": Decimal("0.05"),
        "stop_loss": Decimal("1.0950"),
        "take_profit": Decimal("1.1100"),
        "created_at": NOW,
    }
    values.update(overrides)
    return RiskAssessment(**values)  # type: ignore[arg-type]


def test_risk_assessment_when_approved_is_created() -> None:
    assessment = make_risk_assessment()

    assert assessment.approved is True


def test_risk_assessment_when_rejected_without_reason_rejects_value() -> None:
    with pytest.raises(ValueError, match="rejection codes"):
        make_risk_assessment(
            approved=False,
            risk_amount=Decimal("0"),
            volume=Decimal("0"),
            stop_loss=None,
        )


def test_risk_assessment_when_approved_without_stop_rejects_value() -> None:
    with pytest.raises(ValueError, match="volume and stop_loss"):
        make_risk_assessment(stop_loss=None)


def test_risk_assessment_when_approved_with_rejection_code_rejects_value() -> None:
    with pytest.raises(ValueError, match="cannot contain"):
        make_risk_assessment(rejection_codes=("SPREAD_TOO_WIDE",))


def test_risk_assessment_when_amount_is_negative_rejects_value() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        make_risk_assessment(risk_amount=Decimal("-1"))


def make_order_intent(**overrides: object) -> ApprovedOrderIntent:
    values: dict[str, object] = {
        "id": uuid4(),
        "risk_assessment_id": uuid4(),
        "idempotency_key": "idem-1",
        "mode": TradingMode.PAPER,
        "symbol": "USDJPY",
        "side": Action.BUY,
        "volume": Decimal("0.01"),
        "stop_loss": Decimal("147.000"),
        "take_profit": Decimal("148.000"),
        "created_at": NOW,
    }
    values.update(overrides)
    return ApprovedOrderIntent(**values)  # type: ignore[arg-type]


def test_order_intent_when_valid_is_created() -> None:
    order = make_order_intent()

    assert order.mode is TradingMode.PAPER


def test_order_intent_when_side_is_hold_rejects_value() -> None:
    with pytest.raises(ValueError, match="HOLD"):
        make_order_intent(side=Action.HOLD)


def test_order_intent_when_volume_is_zero_rejects_value() -> None:
    with pytest.raises(ValueError, match="volume"):
        make_order_intent(volume=Decimal("0"))


def test_order_intent_when_idempotency_key_is_blank_rejects_value() -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        make_order_intent(idempotency_key=" ")
