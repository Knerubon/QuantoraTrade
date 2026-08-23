from pathlib import Path

from quantora_trade.infrastructure.database.worker_models import (
    PaperWorkerStateModel,
    PaperWorkerTransitionModel,
)


def test_worker_tables_are_singleton_state_plus_immutable_command_audit() -> None:
    state = PaperWorkerStateModel.__table__
    transition = PaperWorkerTransitionModel.__table__
    assert state.schema == transition.schema == "quantora"
    assert {column.name for column in state.primary_key.columns} == {"id"}
    assert {column.name for column in transition.primary_key.columns} == {"command_id"}
    names = {constraint.name for constraint in state.constraints}
    assert "ck_paper_worker_states_singleton_id" in names
    assert "ck_paper_worker_states_status_valid" in names
    assert "ck_paper_worker_states_config_hash_consistent" in names
    assert "ck_paper_worker_states_lease_consistent" in names
    assert "ck_paper_worker_states_lease_owner_nonempty" in names
    assert "ck_paper_worker_states_lease_window_positive" in names


def test_worker_migration_has_fixed_linear_revision() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations/versions/20260823_0007_paper_worker_runtime.py"
    ).read_text()
    assert 'revision: str = "20260823_0007"' in migration
    assert 'down_revision: str | None = "20260823_0006"' in migration
    assert "CREATE TRIGGER trg_paper_worker_transitions_append_only" in migration
    assert 'sa.Column("active_generation", postgresql.UUID(as_uuid=True)' in migration
