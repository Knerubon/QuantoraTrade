"""Immutable alert and delivery models."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class AlertEvent:
    event_code: str
    severity: AlertSeverity
    component: str
    message: str
    dedup_key: str
    cooldown: timedelta
    occurred_at: datetime
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        for value, name in (
            (self.event_code, "event_code"),
            (self.component, "component"),
            (self.message, "message"),
            (self.dedup_key, "dedup_key"),
        ):
            if not value or value != value.strip():
                raise ValueError(f"{name} must be a non-empty trimmed value")
        if self.cooldown < timedelta(0):
            raise ValueError("cooldown must be non-negative")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != UTC.utcoffset(
            self.occurred_at
        ):
            raise ValueError("occurred_at must be timezone-aware UTC")
        frozen = _freeze(self.payload)
        assert isinstance(frozen, Mapping)
        object.__setattr__(self, "payload", frozen)


class DeliveryOutcome(StrEnum):
    DELIVERED = "delivered"
    SUPPRESSED = "suppressed"
    FAILED = "failed"
