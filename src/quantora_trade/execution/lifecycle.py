"""Strict immutable PAPER order lifecycle rules."""

from types import MappingProxyType

from quantora_trade.execution.models import OrderStatus

ORDER_TRANSITIONS = MappingProxyType(
    {
        OrderStatus.CREATED: frozenset(
            {OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
        ),
        OrderStatus.ACCEPTED: frozenset(
            {
                OrderStatus.PARTIAL,
                OrderStatus.FILLED,
                OrderStatus.CANCEL_PENDING,
                OrderStatus.EXPIRED,
            }
        ),
        OrderStatus.PARTIAL: frozenset(
            {
                OrderStatus.PARTIAL,
                OrderStatus.FILLED,
                OrderStatus.CANCEL_PENDING,
                OrderStatus.EXPIRED,
            }
        ),
        OrderStatus.CANCEL_PENDING: frozenset({OrderStatus.CANCELLED}),
        OrderStatus.FILLED: frozenset(),
        OrderStatus.CANCELLED: frozenset(),
        OrderStatus.REJECTED: frozenset(),
        OrderStatus.EXPIRED: frozenset(),
        OrderStatus.UNKNOWN: frozenset(),
    }
)


class InvalidOrderTransition(ValueError):
    """Raised before an impossible lifecycle mutation can be recorded."""


def require_transition(current: OrderStatus, target: OrderStatus) -> None:
    if target not in ORDER_TRANSITIONS[current]:
        raise InvalidOrderTransition(f"invalid order transition: {current.value} -> {target.value}")
