"""Immutable point-in-time monitoring value objects."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from quantora_trade.domain.enums import TradingMode


def _utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")


def _identifier(value: str, name: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed value")


class ComponentState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ReconciliationState(StrEnum):
    MATCHED = "matched"
    PENDING = "pending"
    MISMATCHED = "mismatched"
    UNKNOWN = "unknown"


class Readiness(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"


@dataclass(frozen=True, slots=True)
class Heartbeat:
    service_name: str
    instance_id: str
    mode: TradingMode
    state: ComponentState
    last_seen_at: datetime
    details: Mapping[str, str]

    def __post_init__(self) -> None:
        _identifier(self.service_name, "service_name")
        _identifier(self.instance_id, "instance_id")
        _utc(self.last_seen_at, "last_seen_at")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True, slots=True)
class ReconciliationStatus:
    component: str
    state: ReconciliationState
    reconciled_at: datetime | None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.component, "component")
        if self.reconciled_at is not None:
            _utc(self.reconciled_at, "reconciled_at")
        if self.reason_code is not None:
            _identifier(self.reason_code, "reason_code")
        if self.state is ReconciliationState.MATCHED and self.reconciled_at is None:
            raise ValueError("matched reconciliation requires reconciled_at")


@dataclass(frozen=True, slots=True, order=True)
class ReadinessReason:
    code: str
    component: str

    def __post_init__(self) -> None:
        _identifier(self.code, "code")
        _identifier(self.component, "component")


@dataclass(frozen=True, slots=True)
class HealthReport:
    readiness: Readiness
    assessed_at: datetime
    reasons: tuple[ReadinessReason, ...]

    def __post_init__(self) -> None:
        _utc(self.assessed_at, "assessed_at")
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("reasons must be unique and deterministically sorted")
        if self.readiness is Readiness.READY and self.reasons:
            raise ValueError("ready report cannot contain reasons")
