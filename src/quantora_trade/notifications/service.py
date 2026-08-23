"""Best-effort alert delivery isolated from trading decisions."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from quantora_trade.notifications.models import AlertEvent, DeliveryOutcome
from quantora_trade.notifications.ports import NotificationPort

_SENSITIVE_KEYS: Final = frozenset(
    {"authorization", "password", "secret", "token", "api_key", "account_number"}
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or any(
        marker in normalized for marker in ("authorization", "password", "secret", "token")
    )


def _sanitize(value: object, *, key: str = "") -> object:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize(item, key=str(item_key)) for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return tuple(_sanitize(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class NotificationService:
    """Deduplicate and deliver alerts; provider failures are returned, never raised."""

    def __init__(self, port: NotificationPort) -> None:
        self._port = port
        self._last_delivered: dict[str, datetime] = {}

    def publish(self, event: AlertEvent, *, now: datetime) -> DeliveryOutcome:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("now must be timezone-aware UTC")
        previous = self._last_delivered.get(event.dedup_key)
        if previous is not None and now < previous + event.cooldown:
            return DeliveryOutcome.SUPPRESSED

        sanitized = _sanitize(dict(event.payload))
        assert isinstance(sanitized, dict)
        try:
            self._port.deliver(event, sanitized)
        except Exception:  # provider errors must never affect trading state
            return DeliveryOutcome.FAILED
        self._last_delivered[event.dedup_key] = now
        return DeliveryOutcome.DELIVERED
