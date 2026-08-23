"""PostgreSQL-backed, atomic kill-switch persistence."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantora_trade.infrastructure.database.kill_switch_models import (
    KillSwitchEventModel,
    KillSwitchStateModel,
)
from quantora_trade.risk.kill_switch import (
    KillSwitchAction,
    KillSwitchEvent,
    KillSwitchQuery,
    KillSwitchScope,
    KillSwitchScopeKind,
    KillSwitchState,
)


class PostgresKillSwitchRepository:
    """Store the immutable event and current state in one database transaction."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get(self, scope: KillSwitchScope) -> KillSwitchState | None:
        with self._session_factory() as session:
            row = session.get(KillSwitchStateModel, scope.key)
            return None if row is None else self._state_from_row(session, row)

    def active_states(self) -> tuple[KillSwitchState, ...]:
        statement = (
            select(KillSwitchStateModel)
            .where(KillSwitchStateModel.active.is_(True))
            .order_by(KillSwitchStateModel.scope_key)
        )
        with self._session_factory() as session:
            return tuple(self._state_from_row(session, row) for row in session.scalars(statement))

    def persist_transition(self, event: KillSwitchEvent, state: KillSwitchState) -> None:
        if event != state.last_event:
            raise ValueError("state must reference the persisted event")

        with self._session_factory() as session, session.begin():
            # Serialize per scope even before its first state row exists. A row
            # lock alone cannot protect two concurrent initial activations.
            session.execute(
                select(func.pg_advisory_xact_lock(func.hashtextextended(state.scope.key, 0)))
            )
            existing_event = session.get(KillSwitchEventModel, event.id)
            if existing_event is not None:
                if self._event_from_row(existing_event) != event:
                    raise ValueError("event id collision")
                return

            current = session.scalar(
                select(KillSwitchStateModel)
                .where(KillSwitchStateModel.scope_key == state.scope.key)
                .with_for_update()
            )
            if current is not None and event.occurred_at <= current.last_transition_at:
                raise ValueError("kill-switch transitions must be strictly monotonic")

            session.add(
                KillSwitchEventModel(
                    id=event.id,
                    action=event.action.value,
                    scope_key=event.scope.key,
                    scope_kind=event.scope.kind.value,
                    scope_value=event.scope.value,
                    occurred_at=event.occurred_at,
                    actor=event.actor,
                    reason=event.reason,
                    incident_reference=event.incident_reference,
                )
            )
            if current is None:
                session.add(
                    KillSwitchStateModel(
                        scope_key=state.scope.key,
                        scope_kind=state.scope.kind.value,
                        scope_value=state.scope.value,
                        active=state.active,
                        last_event_id=event.id,
                        last_transition_at=event.occurred_at,
                    )
                )
            else:
                current.scope_kind = state.scope.kind.value
                current.scope_value = state.scope.value
                current.active = state.active
                current.last_event_id = event.id
                current.last_transition_at = event.occurred_at

    @contextmanager
    def submission_guard(self, query: KillSwitchQuery) -> Iterator[None]:
        """Hold all applicable scope locks across final check and submission."""

        scopes = [
            KillSwitchScope(KillSwitchScopeKind.GLOBAL),
            KillSwitchScope(KillSwitchScopeKind.NEW_ENTRIES),
        ]
        for kind, value in (
            (KillSwitchScopeKind.ACCOUNT, query.account),
            (KillSwitchScopeKind.ASSET, query.asset),
            (KillSwitchScopeKind.SYMBOL, query.symbol),
            (KillSwitchScopeKind.STRATEGY, query.strategy),
        ):
            if value is not None:
                scopes.append(KillSwitchScope(kind, value))
        with self._session_factory() as session, session.begin():
            for scope in sorted(scopes, key=lambda item: item.key):
                session.execute(
                    select(func.pg_advisory_xact_lock(func.hashtextextended(scope.key, 0)))
                )
            yield

    @classmethod
    def _state_from_row(cls, session: Session, row: KillSwitchStateModel) -> KillSwitchState:
        event_row = session.get(KillSwitchEventModel, row.last_event_id)
        if event_row is None:
            raise RuntimeError("kill-switch state references a missing event")
        event = cls._event_from_row(event_row)
        return KillSwitchState(scope=event.scope, active=row.active, last_event=event)

    @staticmethod
    def _event_from_row(row: KillSwitchEventModel) -> KillSwitchEvent:
        occurred_at = row.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        else:
            occurred_at = occurred_at.astimezone(UTC)
        return KillSwitchEvent(
            id=row.id,
            action=KillSwitchAction(row.action),
            scope=KillSwitchScope(KillSwitchScopeKind(row.scope_kind), row.scope_value),
            occurred_at=occurred_at,
            actor=row.actor,
            reason=row.reason,
            incident_reference=row.incident_reference,
        )


__all__ = ["PostgresKillSwitchRepository"]
