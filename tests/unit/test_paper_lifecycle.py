from datetime import UTC, datetime

import pytest

from quantora_trade.execution.lifecycle import (
    ORDER_TRANSITIONS,
    InvalidOrderTransition,
    require_transition,
)
from quantora_trade.execution.models import OrderEvent, OrderStatus


def test_transition_table_covers_every_status_and_terminals_are_closed() -> None:
    assert set(ORDER_TRANSITIONS) == set(OrderStatus)
    for terminal in (
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.UNKNOWN,
    ):
        assert ORDER_TRANSITIONS[terminal] == frozenset()


def test_invalid_transition_is_rejected() -> None:
    with pytest.raises(InvalidOrderTransition, match="filled -> cancelled"):
        require_transition(OrderStatus.FILLED, OrderStatus.CANCELLED)


def test_order_event_requires_utc_and_positive_sequence() -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        OrderEvent(1, OrderStatus.CREATED, datetime(2026, 1, 1), "CREATED")
    with pytest.raises(ValueError, match="sequence"):
        OrderEvent(0, OrderStatus.CREATED, datetime(2026, 1, 1, tzinfo=UTC), "CREATED")
