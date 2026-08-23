from datetime import UTC, datetime, timedelta

import pytest

from quantora_trade.risk.kill_switch import (
    InMemoryKillSwitchRepository,
    KillSwitchAction,
    KillSwitchEvent,
    KillSwitchQuery,
    KillSwitchScope,
    KillSwitchScopeKind,
    KillSwitchService,
    KillSwitchState,
)

NOW = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)


def _activate(service: KillSwitchService, scope: KillSwitchScope) -> KillSwitchState:
    return service.activate(
        scope,
        occurred_at=NOW,
        actor="risk-officer",
        reason="risk threshold breached",
        incident_reference="INC-42",
    )


@pytest.mark.parametrize(
    ("scope", "query", "blocked"),
    [
        (KillSwitchScope(KillSwitchScopeKind.GLOBAL), KillSwitchQuery(new_entry=False), True),
        (KillSwitchScope(KillSwitchScopeKind.NEW_ENTRIES), KillSwitchQuery(), True),
        (
            KillSwitchScope(KillSwitchScopeKind.NEW_ENTRIES),
            KillSwitchQuery(new_entry=False),
            False,
        ),
        (
            KillSwitchScope(KillSwitchScopeKind.ACCOUNT, "acct-1"),
            KillSwitchQuery(account="acct-1"),
            True,
        ),
        (
            KillSwitchScope(KillSwitchScopeKind.ASSET, "FX"),
            KillSwitchQuery(asset="METAL"),
            False,
        ),
        (
            KillSwitchScope(KillSwitchScopeKind.SYMBOL, "XAUUSD"),
            KillSwitchQuery(symbol="XAUUSD"),
            True,
        ),
        (
            KillSwitchScope(KillSwitchScopeKind.STRATEGY, "trend-v1"),
            KillSwitchQuery(strategy="mean-reversion-v1"),
            False,
        ),
    ],
)
def test_scoped_blocking_query(
    scope: KillSwitchScope, query: KillSwitchQuery, blocked: bool
) -> None:
    repository = InMemoryKillSwitchRepository()
    service = KillSwitchService(repository)
    _activate(service, scope)

    assert service.is_blocked(query) is blocked


def test_activation_is_persisted_and_idempotent() -> None:
    repository = InMemoryKillSwitchRepository()
    service = KillSwitchService(repository)
    scope = KillSwitchScope(KillSwitchScopeKind.SYMBOL, "XAUUSD")

    first = _activate(service, scope)
    second = _activate(service, scope)

    assert first is second
    assert repository.get(scope) == first
    assert len(repository.events) == 1
    assert repository.events[0].action is KillSwitchAction.ACTIVATE


def test_failed_persistence_never_exposes_activation() -> None:
    class FailingRepository(InMemoryKillSwitchRepository):
        def persist_transition(self, event: KillSwitchEvent, state: KillSwitchState) -> None:
            raise OSError("database unavailable")

    repository = FailingRepository()
    service = KillSwitchService(repository)
    scope = KillSwitchScope(KillSwitchScopeKind.GLOBAL)

    with pytest.raises(OSError, match="database unavailable"):
        _activate(service, scope)

    assert repository.get(scope) is None


def test_repository_read_failure_fails_closed() -> None:
    class FailingReadRepository(InMemoryKillSwitchRepository):
        def active_states(self) -> tuple[KillSwitchState, ...]:
            raise OSError("database unavailable")

    assert KillSwitchService(FailingReadRepository()).is_blocked(KillSwitchQuery()) is True


def test_deactivation_requires_authorization_and_recovery_reference() -> None:
    repository = InMemoryKillSwitchRepository()
    service = KillSwitchService(repository)
    scope = KillSwitchScope(KillSwitchScopeKind.ACCOUNT, "acct-1")
    _activate(service, scope)

    with pytest.raises(PermissionError, match="higher authorization"):
        service.deactivate(
            scope,
            occurred_at=NOW,
            actor="owner",
            reason="recovered",
            recovery_reference="REC-42",
            higher_authorization=False,
        )
    with pytest.raises(ValueError, match="recovery_reference"):
        service.deactivate(
            scope,
            occurred_at=NOW,
            actor="owner",
            reason="recovered",
            recovery_reference=" ",
            higher_authorization=True,
        )

    state = service.deactivate(
        scope,
        occurred_at=NOW + timedelta(microseconds=1),
        actor="owner",
        reason="incident reviewed and recovered",
        recovery_reference="REC-42",
        higher_authorization=True,
    )

    assert state.active is False
    assert state.last_event.action is KillSwitchAction.DEACTIVATE
    assert service.is_blocked(KillSwitchQuery(account="acct-1")) is False
    assert len(repository.events) == 2


def test_scope_validation_and_immutable_audit_records() -> None:
    with pytest.raises(ValueError, match="must not have a value"):
        KillSwitchScope(KillSwitchScopeKind.GLOBAL, "unexpected")
    with pytest.raises(ValueError, match="requires a value"):
        KillSwitchScope(KillSwitchScopeKind.SYMBOL)

    state = _activate(
        KillSwitchService(InMemoryKillSwitchRepository()),
        KillSwitchScope(KillSwitchScopeKind.GLOBAL),
    )
    with pytest.raises(AttributeError):
        state.active = False  # type: ignore[misc]


def test_transition_clock_cannot_move_backwards() -> None:
    repository = InMemoryKillSwitchRepository()
    service = KillSwitchService(repository)
    scope = KillSwitchScope(KillSwitchScopeKind.SYMBOL, "XAUUSD")
    _activate(service, scope)

    with pytest.raises(ValueError, match="must move forwards"):
        service.deactivate(
            scope,
            occurred_at=NOW - timedelta(microseconds=1),
            actor="owner",
            reason="invalid historical transition",
            recovery_reference="REC-OLD",
            higher_authorization=True,
        )
