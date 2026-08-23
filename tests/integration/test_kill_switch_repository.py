"""PostgreSQL integration tests for durable kill-switch persistence."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from quantora_trade.infrastructure.database.kill_switch_models import KillSwitchEventModel
from quantora_trade.infrastructure.database.kill_switch_repository import (
    PostgresKillSwitchRepository,
)
from quantora_trade.risk.kill_switch import (
    KillSwitchAction,
    KillSwitchEvent,
    KillSwitchQuery,
    KillSwitchScope,
    KillSwitchScopeKind,
    KillSwitchService,
    KillSwitchState,
)

DATABASE_URL = os.getenv("QUANTORA_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("QUANTORA_DATABASE_URL is required for integration tests", allow_module_level=True)

engine = create_engine(DATABASE_URL)
SessionFactory = sessionmaker(engine, expire_on_commit=False)
NOW = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_kill_switch_tables() -> None:
    with SessionFactory() as session, session.begin():
        # Audit DELETE is deliberately blocked. TRUNCATE is reserved for the
        # isolated test database and cannot be reached through the repository.
        session.execute(text("TRUNCATE quantora.kill_switch_states, quantora.kill_switch_events"))


def repository() -> PostgresKillSwitchRepository:
    return PostgresKillSwitchRepository(SessionFactory)


def event_count() -> int:
    with SessionFactory() as session:
        return int(session.scalar(select(func.count()).select_from(KillSwitchEventModel)) or 0)


def test_active_state_survives_repository_and_service_restart() -> None:
    scope = KillSwitchScope(KillSwitchScopeKind.SYMBOL, "XAUUSD")
    KillSwitchService(repository()).activate(
        scope,
        occurred_at=NOW,
        actor="risk-operator",
        reason="abnormal spread",
        incident_reference="INC-42",
    )

    restarted = KillSwitchService(repository())

    assert restarted.is_blocked(KillSwitchQuery(symbol="XAUUSD")) is True
    assert restarted.is_blocked(KillSwitchQuery(symbol="EURUSD")) is False
    restored = repository().get(scope)
    assert restored is not None
    assert restored.last_event.incident_reference == "INC-42"


def test_replaying_identical_event_is_idempotent() -> None:
    scope = KillSwitchScope(KillSwitchScopeKind.GLOBAL)
    service = KillSwitchService(repository())
    state = service.activate(
        scope,
        occurred_at=NOW,
        actor="risk-operator",
        reason="risk incident",
        incident_reference="INC-43",
    )

    repository().persist_transition(state.last_event, state)

    assert event_count() == 1


def test_older_transition_cannot_overwrite_current_state() -> None:
    scope = KillSwitchScope(KillSwitchScopeKind.ACCOUNT, "acct-1")
    current = KillSwitchService(repository()).activate(
        scope,
        occurred_at=NOW,
        actor="risk-operator",
        reason="margin incident",
        incident_reference="INC-44",
    )
    older_event = KillSwitchEvent(
        id=current.last_event.id,
        action=KillSwitchAction.DEACTIVATE,
        scope=scope,
        occurred_at=NOW - timedelta(seconds=1),
        actor="risk-operator",
        reason="stale recovery",
        incident_reference="REC-OLD",
    )
    older_state = KillSwitchState(scope=scope, active=False, last_event=older_event)

    with pytest.raises(ValueError, match="event id collision"):
        repository().persist_transition(older_event, older_state)

    assert repository().get(scope) == current


def test_distinct_older_transition_is_rejected_as_non_monotonic() -> None:
    scope = KillSwitchScope(KillSwitchScopeKind.ACCOUNT, "acct-1")
    KillSwitchService(repository()).activate(
        scope,
        occurred_at=NOW,
        actor="risk-operator",
        reason="margin incident",
        incident_reference="INC-45",
    )
    older_event = KillSwitchEvent(
        id=uuid4(),
        action=KillSwitchAction.DEACTIVATE,
        scope=scope,
        occurred_at=NOW - timedelta(seconds=1),
        actor="risk-operator",
        reason="stale recovery",
        incident_reference="REC-OLD",
    )
    older_state = KillSwitchState(scope=scope, active=False, last_event=older_event)

    with pytest.raises(ValueError, match="strictly monotonic"):
        repository().persist_transition(older_event, older_state)

    restored = repository().get(scope)
    assert restored is not None
    assert restored.active is True


def test_audit_events_cannot_be_updated_or_deleted() -> None:
    state = KillSwitchService(repository()).activate(
        KillSwitchScope(KillSwitchScopeKind.GLOBAL),
        occurred_at=NOW,
        actor="risk-operator",
        reason="risk incident",
        incident_reference="INC-IMMUTABLE",
    )

    for statement in (
        text("UPDATE quantora.kill_switch_events SET reason = 'tampered' WHERE id = :id"),
        text("DELETE FROM quantora.kill_switch_events WHERE id = :id"),
    ):
        with (
            SessionFactory() as session,
            session.begin(),
            pytest.raises(DBAPIError, match="append-only"),
        ):
            session.execute(statement, {"id": state.last_event.id})


def test_state_cannot_disagree_with_referenced_event() -> None:
    state = KillSwitchService(repository()).activate(
        KillSwitchScope(KillSwitchScopeKind.SYMBOL, "XAUUSD"),
        occurred_at=NOW,
        actor="risk-operator",
        reason="spread incident",
        incident_reference="INC-CONSISTENCY",
    )

    with (
        SessionFactory() as session,
        session.begin(),
        pytest.raises(DBAPIError, match="does not agree"),
    ):
        session.execute(
            text(
                "UPDATE quantora.kill_switch_states SET active = false WHERE scope_key = :scope_key"
            ),
            {"scope_key": state.scope.key},
        )


def test_concurrent_equal_timestamp_transitions_have_one_winner() -> None:
    scope = KillSwitchScope(KillSwitchScopeKind.ACCOUNT, "acct-concurrent")
    barrier = Barrier(2)

    def persist(reference: str) -> str:
        event = KillSwitchEvent(
            id=uuid4(),
            action=KillSwitchAction.ACTIVATE,
            scope=scope,
            occurred_at=NOW,
            actor="risk-operator",
            reason="concurrent incident",
            incident_reference=reference,
        )
        state = KillSwitchState(scope=scope, active=True, last_event=event)
        barrier.wait()
        try:
            repository().persist_transition(event, state)
        except ValueError as error:
            return str(error)
        return "persisted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(persist, ("INC-RACE-A", "INC-RACE-B")))

    assert sorted(outcomes) == ["kill-switch transitions must be strictly monotonic", "persisted"]
    assert event_count() == 1
