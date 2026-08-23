"""Durable, audited kill-switch boundary for blocking new trading activity.

This module deliberately has no execution or broker capability.  A kill switch
answers only whether an operation is blocked; it never closes an open position.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5


class KillSwitchScopeKind(StrEnum):
    GLOBAL = "global"
    ACCOUNT = "account"
    ASSET = "asset"
    SYMBOL = "symbol"
    STRATEGY = "strategy"
    NEW_ENTRIES = "new_entries"


class KillSwitchAction(StrEnum):
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"


@dataclass(frozen=True, slots=True)
class KillSwitchScope:
    kind: KillSwitchScopeKind
    value: str | None = None

    def __post_init__(self) -> None:
        unqualified = {KillSwitchScopeKind.GLOBAL, KillSwitchScopeKind.NEW_ENTRIES}
        if self.kind in unqualified and self.value is not None:
            raise ValueError(f"{self.kind.value} scope must not have a value")
        if self.kind not in unqualified and (self.value is None or not self.value.strip()):
            raise ValueError(f"{self.kind.value} scope requires a value")
        if self.value is not None:
            if self.value != self.value.strip():
                raise ValueError(f"{self.kind.value} scope value must be trimmed")
            if self.kind in {KillSwitchScopeKind.ASSET, KillSwitchScopeKind.SYMBOL} and (
                self.value != self.value.upper()
            ):
                raise ValueError(f"{self.kind.value} scope value must be canonical uppercase")

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.value or '*'}"


@dataclass(frozen=True, slots=True)
class KillSwitchEvent:
    id: UUID
    action: KillSwitchAction
    scope: KillSwitchScope
    occurred_at: datetime
    actor: str
    reason: str
    incident_reference: str

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != UTC.utcoffset(
            self.occurred_at
        ):
            raise ValueError("occurred_at must be timezone-aware UTC")
        for name in ("actor", "reason", "incident_reference"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    scope: KillSwitchScope
    active: bool
    last_event: KillSwitchEvent

    def __post_init__(self) -> None:
        if self.scope != self.last_event.scope:
            raise ValueError("state and event scopes must match")
        expected = self.last_event.action is KillSwitchAction.ACTIVATE
        if self.active is not expected:
            raise ValueError("state must agree with the last event action")


@dataclass(frozen=True, slots=True)
class KillSwitchQuery:
    account: str | None = None
    asset: str | None = None
    symbol: str | None = None
    strategy: str | None = None
    new_entry: bool = True


class KillSwitchRepository(Protocol):
    """Persistence port; transition storage must be atomic and durable."""

    def get(self, scope: KillSwitchScope) -> KillSwitchState | None: ...

    def active_states(self) -> tuple[KillSwitchState, ...]: ...

    def persist_transition(self, event: KillSwitchEvent, state: KillSwitchState) -> None: ...

    def submission_guard(self, query: KillSwitchQuery) -> AbstractContextManager[None]: ...


class InMemoryKillSwitchRepository:
    """Deterministic fake with an append-only audit log for unit tests."""

    def __init__(self) -> None:
        self._states: dict[str, KillSwitchState] = {}
        self._events: dict[UUID, KillSwitchEvent] = {}
        self._lock = RLock()

    @property
    def events(self) -> tuple[KillSwitchEvent, ...]:
        return tuple(self._events.values())

    def get(self, scope: KillSwitchScope) -> KillSwitchState | None:
        return self._states.get(scope.key)

    def active_states(self) -> tuple[KillSwitchState, ...]:
        return tuple(state for state in self._states.values() if state.active)

    def persist_transition(self, event: KillSwitchEvent, state: KillSwitchState) -> None:
        with self._lock:
            existing = self._events.get(event.id)
            if existing is not None:
                if existing != event:
                    raise ValueError("event id collision")
                return
            current = self._states.get(state.scope.key)
            if current is not None and event.occurred_at <= current.last_event.occurred_at:
                raise ValueError("kill-switch transitions must be strictly monotonic")
            self._events[event.id] = event
            self._states[state.scope.key] = state

    @contextmanager
    def submission_guard(self, query: KillSwitchQuery) -> Iterator[None]:
        with self._lock:
            yield


class KillSwitchService:
    """Persist audited transitions and perform side-effect-free blocking queries."""

    def __init__(self, repository: KillSwitchRepository) -> None:
        self._repository = repository

    def activate(
        self,
        scope: KillSwitchScope,
        *,
        occurred_at: datetime,
        actor: str,
        reason: str,
        incident_reference: str,
    ) -> KillSwitchState:
        current = self._repository.get(scope)
        if current is not None and current.active:
            return current
        if current is not None and occurred_at <= current.last_event.occurred_at:
            raise ValueError("kill-switch transition time must move forwards")
        event = self._event(
            KillSwitchAction.ACTIVATE,
            scope,
            occurred_at,
            actor,
            reason,
            incident_reference,
        )
        state = KillSwitchState(scope=scope, active=True, last_event=event)
        # State becomes observable only after the repository confirms persistence.
        self._repository.persist_transition(event, state)
        return state

    def deactivate(
        self,
        scope: KillSwitchScope,
        *,
        occurred_at: datetime,
        actor: str,
        reason: str,
        recovery_reference: str,
        higher_authorization: bool,
    ) -> KillSwitchState:
        if not higher_authorization:
            raise PermissionError("kill-switch deactivation requires higher authorization")
        if not recovery_reference.strip():
            raise ValueError("recovery_reference must not be empty")
        current = self._repository.get(scope)
        if current is None or not current.active:
            raise ValueError("kill switch is not active for this scope")
        if occurred_at <= current.last_event.occurred_at:
            raise ValueError("kill-switch transition time must move forwards")
        event = self._event(
            KillSwitchAction.DEACTIVATE,
            scope,
            occurred_at,
            actor,
            reason,
            recovery_reference,
        )
        state = KillSwitchState(scope=scope, active=False, last_event=event)
        self._repository.persist_transition(event, state)
        return state

    def is_blocked(self, query: KillSwitchQuery) -> bool:
        """Return ``True`` on a matching switch or any persistence uncertainty."""

        try:
            states = self._repository.active_states()
        except Exception:  # repository failures must fail closed
            return True
        return any(self._matches(state.scope, query) for state in states)

    def submission_guard(self, query: KillSwitchQuery) -> AbstractContextManager[None]:
        """Serialize the final check/submit against switch transitions."""

        try:
            return self._repository.submission_guard(query)
        except Exception as error:
            raise PermissionError("kill-switch submission guard unavailable") from error

    @staticmethod
    def _matches(scope: KillSwitchScope, query: KillSwitchQuery) -> bool:
        values = {
            KillSwitchScopeKind.ACCOUNT: query.account,
            KillSwitchScopeKind.ASSET: query.asset,
            KillSwitchScopeKind.SYMBOL: query.symbol,
            KillSwitchScopeKind.STRATEGY: query.strategy,
        }
        if scope.kind is KillSwitchScopeKind.GLOBAL:
            return True
        if scope.kind is KillSwitchScopeKind.NEW_ENTRIES:
            return query.new_entry
        return values[scope.kind] == scope.value

    @staticmethod
    def _event(
        action: KillSwitchAction,
        scope: KillSwitchScope,
        occurred_at: datetime,
        actor: str,
        reason: str,
        reference: str,
    ) -> KillSwitchEvent:
        identity = "|".join(
            (action.value, scope.key, occurred_at.isoformat(), actor, reason, reference)
        )
        return KillSwitchEvent(
            id=uuid5(NAMESPACE_URL, identity),
            action=action,
            scope=scope,
            occurred_at=occurred_at,
            actor=actor,
            reason=reason,
            incident_reference=reference,
        )
