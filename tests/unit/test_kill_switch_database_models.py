"""Unit checks for kill-switch database/domain mapping."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import CheckConstraint

from quantora_trade.infrastructure.database.kill_switch_models import (
    KillSwitchEventModel,
    KillSwitchStateModel,
)
from quantora_trade.infrastructure.database.kill_switch_repository import (
    PostgresKillSwitchRepository,
)
from quantora_trade.risk.kill_switch import KillSwitchAction, KillSwitchScopeKind


def test_event_row_maps_back_to_utc_domain_event() -> None:
    row = KillSwitchEventModel(
        id=UUID("b3bb8a1d-bf90-4f89-beb1-0aada905b1de"),
        action="activate",
        scope_key="symbol:XAUUSD",
        scope_kind="symbol",
        scope_value="XAUUSD",
        occurred_at=datetime(2026, 8, 23, 3, 0),
        actor="risk-operator",
        reason="spread anomaly",
        incident_reference="INC-42",
    )

    event = PostgresKillSwitchRepository._event_from_row(row)

    assert event.action is KillSwitchAction.ACTIVATE
    assert event.scope.kind is KillSwitchScopeKind.SYMBOL
    assert event.scope.value == "XAUUSD"
    assert event.occurred_at == datetime(2026, 8, 23, 3, 0, tzinfo=UTC)
    assert event.incident_reference == "INC-42"


def test_database_models_constrain_canonical_scope_keys() -> None:
    for model in (KillSwitchEventModel, KillSwitchStateModel):
        checks = {
            str(constraint.sqltext)
            for constraint in model.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert "scope_key = scope_kind || ':' || COALESCE(scope_value, '*')" in checks


def test_migration_installs_and_removes_database_guard_triggers() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations/versions/20260823_0003_kill_switch.py"
    ).read_text(encoding="utf-8")

    assert "CREATE TRIGGER trg_kill_switch_events_append_only" in migration
    assert "CREATE TRIGGER trg_kill_switch_states_validate_event" in migration
    assert "DROP TRIGGER IF EXISTS trg_kill_switch_events_append_only" in migration
    assert "DROP TRIGGER IF EXISTS trg_kill_switch_states_validate_event" in migration
