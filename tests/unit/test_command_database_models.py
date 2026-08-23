"""Schema checks for the durable control-command queue."""

from quantora_trade.infrastructure.database.command_models import SystemCommandModel


def test_system_command_table_has_durable_identity_and_safety_constraints() -> None:
    table = SystemCommandModel.__table__
    assert set(table.primary_key.columns.keys()) == {"id"}
    assert {
        "payload",
        "queue_sequence",
        "actor",
        "created_at",
        "updated_at",
        "worker_id",
        "lease_token",
        "lease_expires_at",
        "last_heartbeat_at",
        "attempts",
        "completed_at",
    } <= set(table.columns.keys())
    names = {constraint.name for constraint in table.constraints}
    assert "ck_system_commands_action_valid" in names
    assert "ck_system_commands_paper_mode_only" in names
    assert "ck_system_commands_status_valid" in names
    assert "ck_system_commands_request_hash_valid" in names
    assert "ck_system_commands_state_consistent" in names
    assert "ck_system_commands_timestamps_monotonic" in names
    assert "uq_system_commands_actor_idempotency_key_unique" in names
