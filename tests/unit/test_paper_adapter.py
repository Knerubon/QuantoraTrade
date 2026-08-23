from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.execution.lifecycle import InvalidOrderTransition
from quantora_trade.execution.models import (
    InstrumentExecutionSnapshot,
    OrderStatus,
    PaperOrderRequest,
    PaperQuote,
)
from quantora_trade.execution.paper import (
    DeterministicPaperAdapter,
    IdempotencyConflict,
    PaperFillPolicy,
)


@dataclass
class MutableClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


NOW = datetime(2026, 8, 23, 4, tzinfo=UTC)


def request(**changes: object) -> PaperOrderRequest:
    values: dict[str, object] = {
        "approved_intent_id": UUID("00000000-0000-0000-0000-000000000001"),
        "idempotency_key": "paper-key-1",
        "mode": TradingMode.PAPER,
        "symbol": "XAUUSD",
        "side": Action.BUY,
        "volume": Decimal("1.00"),
        "instrument": InstrumentExecutionSnapshot(
            UUID(int=2), UUID(int=3), "a" * 64, "USD", Decimal("100"), Decimal("0.01")
        ),
        "expires_at": NOW + timedelta(minutes=5),
    }
    values.update(changes)
    return PaperOrderRequest(**values)  # type: ignore[arg-type]


def quote(**changes: object) -> PaperQuote:
    values: dict[str, object] = {
        "symbol": "XAUUSD",
        "bid": Decimal("2500.00"),
        "ask": Decimal("2500.20"),
        "available_volume": Decimal("1.00"),
        "observed_at": NOW,
    }
    values.update(changes)
    return PaperQuote(**values)  # type: ignore[arg-type]


def adapter(clock: MutableClock) -> DeterministicPaperAdapter:
    return DeterministicPaperAdapter(
        clock=clock,
        policy=PaperFillPolicy(
            slippage_points=2,
            commission_per_volume=Decimal("3.5"),
        ),
    )


def test_paper_only_and_model_validation() -> None:
    with pytest.raises(ValueError, match="PAPER mode only"):
        request(mode=TradingMode.LIVE)
    with pytest.raises(ValueError, match="BUY or SELL"):
        request(side=Action.HOLD)
    with pytest.raises(ValueError, match="canonical uppercase"):
        request(symbol="xauusd")
    with pytest.raises(ValueError, match="ask must not"):
        quote(bid=Decimal("2"), ask=Decimal("1"))


def test_buy_fill_uses_ask_slippage_commission_and_append_only_events() -> None:
    clock = MutableClock(NOW)
    result = adapter(clock).submit(request(), quote())

    assert result.status is OrderStatus.FILLED
    assert result.filled_volume == Decimal("1.00")
    assert result.fills[0].price == Decimal("2500.22")
    assert result.fills[0].commission == Decimal("3.500")
    assert [event.status for event in result.events] == [
        OrderStatus.CREATED,
        OrderStatus.ACCEPTED,
        OrderStatus.FILLED,
    ]
    assert [event.sequence for event in result.events] == [1, 2, 3]


def test_sell_uses_bid_minus_slippage() -> None:
    clock = MutableClock(NOW)
    result = adapter(clock).submit(request(side=Action.SELL), quote())
    assert result.fills[0].price == Decimal("2499.98")


def test_idempotent_replay_returns_same_order_and_conflict_rejects() -> None:
    clock = MutableClock(NOW)
    paper = adapter(clock)
    first = paper.submit(request(), quote())
    replay = paper.submit(request(), quote(ask=Decimal("9999")))
    assert replay is first

    with pytest.raises(IdempotencyConflict):
        paper.submit(request(volume=Decimal("2")), quote())


def test_instrument_snapshot_is_bound_into_hash_and_idempotency_identity() -> None:
    clock = MutableClock(NOW)
    paper = adapter(clock)
    original = request()
    first = paper.submit(original, quote())
    changed = replace(
        original,
        instrument=replace(original.instrument, specification_hash="b" * 64),
    )

    assert first.request.instrument.specification_hash == "a" * 64
    with pytest.raises(IdempotencyConflict):
        paper.submit(changed, quote())


def test_partial_fill_then_add_liquidity_preserves_event_history() -> None:
    clock = MutableClock(NOW)
    paper = adapter(clock)
    partial = paper.submit(request(), quote(available_volume=Decimal("0.25")))
    assert partial.status is OrderStatus.PARTIAL
    assert partial.remaining_volume == Decimal("0.75")
    old_events = partial.events

    clock.value += timedelta(seconds=1)
    filled = paper.add_liquidity(
        "paper-key-1",
        quote(available_volume=Decimal("0.75"), observed_at=clock.value),
    )
    assert filled.status is OrderStatus.FILLED
    assert filled.filled_volume == Decimal("1.00")
    assert filled.events[: len(old_events)] == old_events
    assert len(filled.fills) == 2


def test_cancel_partial_order_records_pending_and_cancelled() -> None:
    clock = MutableClock(NOW)
    paper = adapter(clock)
    paper.submit(request(), quote(available_volume=Decimal("0.25")))
    result = paper.cancel("paper-key-1")
    assert result.status is OrderStatus.CANCELLED
    assert [event.status for event in result.events[-2:]] == [
        OrderStatus.CANCEL_PENDING,
        OrderStatus.CANCELLED,
    ]
    with pytest.raises(InvalidOrderTransition):
        paper.cancel("paper-key-1")


def test_expired_at_submit_and_expiry_after_partial() -> None:
    clock = MutableClock(NOW)
    paper = adapter(clock)
    expired = paper.submit(request(expires_at=NOW), quote())
    assert expired.status is OrderStatus.EXPIRED
    assert expired.fills == ()

    second = adapter(clock)
    second.submit(
        request(idempotency_key="second"),
        quote(available_volume=Decimal("0.25")),
    )
    clock.value = NOW + timedelta(minutes=5)
    result = second.expire("second")
    assert result.status is OrderStatus.EXPIRED
    assert result.filled_volume == Decimal("0.25")


def test_rejects_bad_quote_and_zero_liquidity_remains_accepted() -> None:
    clock = MutableClock(NOW)
    paper = adapter(clock)
    mismatch = paper.submit(request(), quote(symbol="EURUSD"))
    assert mismatch.status is OrderStatus.REJECTED

    other = adapter(clock)
    accepted = other.submit(request(), quote(available_volume=Decimal("0")))
    assert accepted.status is OrderStatus.ACCEPTED


def test_future_quote_rejected_and_expire_too_early_fails() -> None:
    clock = MutableClock(NOW)
    paper = adapter(clock)
    future = paper.submit(request(), quote(observed_at=NOW + timedelta(seconds=1)))
    assert future.status is OrderStatus.REJECTED

    paper2 = adapter(clock)
    paper2.submit(request(), quote(available_volume=Decimal("0")))
    with pytest.raises(ValueError, match="not reached"):
        paper2.expire("paper-key-1")


def test_stale_quotes_cannot_fill_on_submit_or_add_liquidity() -> None:
    clock = MutableClock(NOW)
    paper = adapter(clock)
    stale = paper.submit(request(), quote(observed_at=NOW - timedelta(seconds=31)))
    assert stale.status is OrderStatus.REJECTED
    assert stale.events[-1].code == "QUOTE_TOO_OLD"

    partial = adapter(clock)
    partial.submit(request(), quote(available_volume=Decimal("0")))
    clock.value += timedelta(seconds=31)
    with pytest.raises(ValueError, match="QUOTE_TOO_OLD"):
        partial.add_liquidity("paper-key-1", quote(observed_at=NOW))
    with pytest.raises(ValueError, match="QUOTE_FROM_FUTURE"):
        partial.add_liquidity("paper-key-1", quote(observed_at=clock.value + timedelta(seconds=1)))
    assert partial.get("paper-key-1").filled_volume == 0


def test_unknown_order_and_invalid_policy() -> None:
    clock = MutableClock(NOW)
    paper = adapter(clock)
    with pytest.raises(KeyError, match="not found"):
        paper.get("missing")
    with pytest.raises(ValueError, match="slippage_points"):
        PaperFillPolicy(slippage_points=-1)
    with pytest.raises(ValueError, match="max_quote_age"):
        PaperFillPolicy(max_quote_age=timedelta(0))
