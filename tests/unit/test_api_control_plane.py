"""Tests for the Phase 6 API boundary."""

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quantora_trade.api import create_app
from quantora_trade.api.schemas import ResolvedSymbolSpecification, ServiceStatus
from quantora_trade.domain.enums import TradingMode
from quantora_trade.infrastructure.database.command_repository import (
    CommandStatus,
    DurableSystemCommand,
    EnqueueResult,
    IdempotencyConflictError,
)


class FakeAuthorizer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def authorize(self, bearer_token: str, required_scope: str) -> str:
        self.calls.append((bearer_token, required_scope))
        if bearer_token != "paper-token":
            raise PermissionError("scope denied")
        return "operator@example.test"


class MemoryCommandRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, DurableSystemCommand] = {}
        self.keys: dict[tuple[str, str], DurableSystemCommand] = {}

    def enqueue(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        request_hash: str,
        action: str,
        mode: str,
        payload: Mapping[str, object],
        actor: str,
    ) -> EnqueueResult:
        key = (actor, idempotency_key)
        prior = self.keys.get(key)
        if prior is not None:
            if prior.request_hash != request_hash:
                raise IdempotencyConflictError("idempotency key conflict")
            return EnqueueResult(prior, False)
        now = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)
        command = DurableSystemCommand(
            id=uuid4(),
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            action=action,
            mode=mode,
            payload=dict(payload),
            actor=actor,
            status=CommandStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        self.keys[key] = command
        self.by_id[command.id] = command
        return EnqueueResult(command, True)

    def get(self, command_id: UUID) -> DurableSystemCommand | None:
        return self.by_id.get(command_id)


class SymbolPreflight:
    def __init__(self, *, overrides: dict[str, dict[str, object]] | None = None) -> None:
        self.overrides = overrides or {}

    def resolve(self, symbols: Sequence[str]) -> tuple[ResolvedSymbolSpecification, ...]:
        defaults = {"XAUUSD": "USD", "EURUSD": "USD", "USDJPY": "JPY"}
        return tuple(
            ResolvedSymbolSpecification.model_validate(
                {
                    "symbol": symbol,
                    "specification_id": UUID(int=index + 1),
                    "specification_hash": f"{index + 1:064x}",
                    "quote_currency": defaults[symbol],
                    **self.overrides.get(symbol, {}),
                }
            )
            for index, symbol in enumerate(symbols)
            if symbol in defaults
        )


def control_app(
    repository: MemoryCommandRepository, authorizer: FakeAuthorizer | None = None
) -> FastAPI:
    return create_app(
        command_repository=repository,
        authorizer=authorizer or FakeAuthorizer(),
        symbol_preflight=SymbolPreflight(),
    )


def snapshot(*, ready: bool = True) -> ServiceStatus:
    return ServiceStatus(
        version="test",
        environment="test",
        mode=TradingMode.PAPER,
        ready=ready,
        database_ready=ready,
        broker_connected=False,
        kill_switch_active=True,
    )


def complete_policy() -> dict[str, object]:
    return {
        "version": "risk-paper-v1",
        "risk_per_trade": "0.005",
        "daily_loss_limit": "0.02",
        "max_drawdown": "0.10",
        "max_portfolio_open_risk": "0.04",
        "max_open_positions": 3,
        "max_spread_points": 50,
        "max_slippage_points": 10,
        "min_stop_ticks": "10",
        "max_stop_ticks": "1000",
        "min_reward_risk": "1.5",
        "max_consecutive_losses": 3,
        "cooldown_seconds": 7200,
        "minimum_margin_buffer": "0.2",
        "snapshot_max_age_seconds": 30,
    }


def test_health_and_secret_free_status_use_injected_snapshot() -> None:
    client = TestClient(create_app(lambda: snapshot(), authorizer=FakeAuthorizer()))

    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/health/ready").json() == {"status": "ready"}
    assert client.get("/status").status_code == 401
    body = client.get("/status", headers={"Authorization": "Bearer paper-token"}).json()
    assert body["mode"] == "paper"
    assert body["kill_switch_active"] is True
    assert "database_url" not in body
    assert "secret" not in str(body).lower()


def test_not_ready_is_fail_closed() -> None:
    response = TestClient(create_app(lambda: snapshot(ready=False))).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "service is not ready"}


def test_incomplete_policy_is_valid_draft_but_not_activation_ready() -> None:
    response = TestClient(create_app()).post(
        "/config/risk/validate",
        json={"policy": {"version": "draft-v1", "risk_per_trade": "0.005"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["structurally_valid"] is True
    assert body["activation_ready"] is False
    assert "daily_loss_limit" in body["missing_limits"]
    assert body["policy"]["risk_per_trade"] == "0.005"


def test_complete_policy_is_ready_and_decimal_values_remain_strings() -> None:
    response = TestClient(create_app()).post(
        "/config/risk/validate",
        json={"policy": complete_policy(), "requested_mode": "paper"},
    )

    assert response.status_code == 200
    assert response.json()["activation_ready"] is True
    assert response.json()["policy"]["minimum_margin_buffer"] == "0.2"


def test_non_paper_modes_and_activation_are_explicitly_rejected() -> None:
    client = TestClient(create_app())

    live = client.post(
        "/config/risk/validate",
        json={"policy": complete_policy(), "requested_mode": "live"},
    )
    backtest = client.post(
        "/config/risk/validate",
        json={"policy": complete_policy(), "requested_mode": "backtest"},
    )
    start = client.post(
        "/config/risk/validate",
        json={"policy": complete_policy(), "activate": True},
    )
    assert live.status_code == 403
    assert live.json() == {"detail": "paper mode is required"}
    assert backtest.status_code == 403
    assert backtest.json() == {"detail": "paper mode is required"}
    assert start.status_code == 409


def test_unknown_config_and_numeric_decimal_are_rejected() -> None:
    client = TestClient(create_app())
    arbitrary = client.post(
        "/config/risk/validate",
        json={"policy": {"version": "draft", "database_url": "secret"}},
    )
    numeric = client.post(
        "/config/risk/validate",
        json={"policy": {"version": "draft", "risk_per_trade": 0.01}},
    )

    assert arbitrary.status_code == 422
    assert numeric.status_code == 422


def test_api_has_no_order_or_execution_endpoint() -> None:
    paths = create_app().openapi()["paths"]

    assert all("order" not in path for path in paths)
    assert all("execute" not in path for path in paths)


def command_body(*, mode: str = "paper", reason: str = "operator requested") -> dict[str, object]:
    return {
        "mode": mode,
        "symbols": ["XAUUSD", "EURUSD"],
        "strategy_id": "trend-v1",
        "reason": reason,
    }


def command_headers(*, key: str = "key-1") -> dict[str, str]:
    return {
        "Authorization": "Bearer paper-token",
        "X-Request-ID": "request-1",
        "Idempotency-Key": key,
    }


def test_start_only_enqueues_typed_paper_command_and_records_audit_actor() -> None:
    repository = MemoryCommandRepository()
    authorizer = FakeAuthorizer()
    client = TestClient(control_app(repository, authorizer))

    response = client.post("/system/start", json=command_body(), headers=command_headers())

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["actor"] == "operator@example.test"
    assert body["symbols"] == ["XAUUSD", "EURUSD"]
    assert body["replayed"] is False
    assert authorizer.calls == [("paper-token", "system:operate")]
    assert len(repository.by_id) == 1


def test_same_idempotency_request_replays_and_changed_request_conflicts() -> None:
    repository = MemoryCommandRepository()
    client = TestClient(control_app(repository))

    first = client.post("/system/stop", json=command_body(), headers=command_headers())
    replay = client.post("/system/stop", json=command_body(), headers=command_headers())
    conflict = client.post(
        "/system/stop",
        json=command_body(reason="different reason"),
        headers=command_headers(),
    )

    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    assert replay.json()["replayed"] is True
    assert conflict.status_code == 409
    assert len(repository.by_id) == 1


def test_live_auth_missing_headers_and_noncanonical_symbols_fail_closed() -> None:
    client = TestClient(control_app(MemoryCommandRepository()))

    live = client.post("/system/start", json=command_body(mode="live"), headers=command_headers())
    backtest = client.post(
        "/system/start", json=command_body(mode="backtest"), headers=command_headers()
    )
    missing_headers = client.post("/system/start", json=command_body())
    bad_symbol = client.post(
        "/system/start",
        json={**command_body(), "symbols": ["xauusd"]},
        headers=command_headers(),
    )
    bad_token = client.post(
        "/system/start",
        json=command_body(),
        headers={**command_headers(), "Authorization": "Bearer wrong"},
    )

    assert live.status_code == 403
    assert backtest.status_code == 403
    assert missing_headers.status_code == 422
    assert bad_symbol.status_code == 422
    assert bad_token.status_code == 403
    assert bad_token.json() == {"detail": "insufficient authorization scope"}


def test_command_status_requires_read_scope_and_hides_queue_secrets() -> None:
    repository = MemoryCommandRepository()
    authorizer = FakeAuthorizer()
    client = TestClient(control_app(repository, authorizer))
    accepted = client.post("/system/start", json=command_body(), headers=command_headers())

    response = client.get(
        f"/system/commands/{accepted.json()['id']}",
        headers={"Authorization": "Bearer paper-token"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert "idempotency" not in response.text.lower()
    assert "request_hash" not in response.text
    assert authorizer.calls[-1] == ("paper-token", "system:read")


def test_request_hash_is_canonical_sha256_and_includes_action() -> None:
    repository = MemoryCommandRepository()
    client = TestClient(control_app(repository))
    client.post("/system/start", json=command_body(), headers=command_headers())

    command = next(iter(repository.by_id.values()))
    assert len(command.request_hash) == hashlib.sha256().digest_size * 2
    stop = client.post("/system/stop", json=command_body(), headers=command_headers(key="key-2"))
    assert stop.status_code == 202
    hashes = {item.request_hash for item in repository.by_id.values()}
    assert len(hashes) == 2


def test_versioned_routes_and_contract_headers_preserve_root_aliases() -> None:
    client = TestClient(create_app(lambda: snapshot()))

    versioned = client.get("/api/v1/health/live", headers={"X-Request-ID": "req-client"})
    compatibility = client.get("/health/live")

    assert versioned.status_code == compatibility.status_code == 200
    assert versioned.headers["X-Request-ID"] == "req-client"
    assert versioned.headers["X-API-Version"] == "1"
    assert compatibility.headers["X-Request-ID"].startswith("req_")


def test_authoritative_preflight_accepts_xauusd_and_eurusd_with_same_quote() -> None:
    repository = MemoryCommandRepository()
    response = TestClient(control_app(repository)).post(
        "/system/start", json=command_body(), headers=command_headers()
    )

    assert response.status_code == 202
    persisted = cast(
        list[dict[str, str]],
        next(iter(repository.by_id.values())).payload["symbol_specifications"],
    )
    assert [item["quote_currency"] for item in persisted] == ["USD", "USD"]
    assert all(item["specification_id"] and item["specification_hash"] for item in persisted)


def test_authoritative_preflight_rejects_mixed_quote_currencies() -> None:
    body = command_body()
    body["symbols"] = ["EURUSD", "USDJPY"]
    response = TestClient(control_app(MemoryCommandRepository())).post(
        "/system/start", json=body, headers=command_headers()
    )

    assert response.status_code == 422
    assert "all quote currencies must match" in response.text


def test_authoritative_preflight_rejects_unknown_suffix_ambiguous_and_stale() -> None:
    repository = MemoryCommandRepository()
    headers = command_headers()
    unknown = {**command_body(), "symbols": ["XAUUSD.PRO"]}
    missing = TestClient(control_app(repository)).post(
        "/system/start", json=unknown, headers=headers
    )
    stale_app = create_app(
        command_repository=MemoryCommandRepository(),
        authorizer=FakeAuthorizer(),
        symbol_preflight=SymbolPreflight(overrides={"XAUUSD": {"stale": True}}),
    )
    stale = TestClient(stale_app).post("/system/start", json=command_body(), headers=headers)

    assert missing.status_code == 422
    assert "resolve exactly once" in missing.text
    assert stale.status_code == 422
    assert "stale specification" in stale.text


def test_start_without_authoritative_preflight_is_unavailable_but_stop_is_allowed() -> None:
    client = TestClient(
        create_app(command_repository=MemoryCommandRepository(), authorizer=FakeAuthorizer())
    )
    start = client.post("/system/start", json=command_body(), headers=command_headers())
    stop = client.post("/system/stop", json=command_body(), headers=command_headers(key="stop-1"))

    assert start.status_code == 503
    assert stop.status_code == 202


def test_versioned_commands_use_documented_operate_scope() -> None:
    repository = MemoryCommandRepository()
    authorizer = FakeAuthorizer()
    response = TestClient(control_app(repository, authorizer)).post(
        "/api/v1/system/start", json=command_body(), headers=command_headers()
    )

    assert response.status_code == 202
    assert authorizer.calls == [("paper-token", "system:operate")]


def test_versioned_status_requires_documented_read_scope() -> None:
    authorizer = FakeAuthorizer()
    client = TestClient(create_app(lambda: snapshot(), authorizer=authorizer))

    denied = client.get("/api/v1/status")
    allowed = client.get("/api/v1/status", headers={"Authorization": "Bearer paper-token"})

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert authorizer.calls == [("paper-token", "system:read")]
