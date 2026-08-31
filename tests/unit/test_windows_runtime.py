from datetime import UTC, datetime, timedelta

import pytest

from quantora_trade.application.paper_operations import CurrentMarketInput
from quantora_trade.runtime.windows import EnvironmentTokenAuthorizer, ObservationOnlyRunner


def test_environment_authorizer_accepts_only_known_local_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUANTORA_API_TOKEN", "a-secure-local-token-value")
    authorizer = EnvironmentTokenAuthorizer()

    assert authorizer.authorize("a-secure-local-token-value", "system:read") == (
        "windows-paper-operator"
    )
    with pytest.raises(PermissionError):
        authorizer.authorize("wrong-token", "system:read")
    with pytest.raises(PermissionError):
        authorizer.authorize("a-secure-local-token-value", "system:admin")


def test_environment_authorizer_rejects_short_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANTORA_API_TOKEN", "too-short")
    with pytest.raises(RuntimeError, match="24 characters"):
        EnvironmentTokenAuthorizer()


def test_observation_runner_never_attempts_orders() -> None:
    report = ObservationOnlyRunner().run_cycle(
        CurrentMarketInput("XAUUSD", datetime.now(UTC) - timedelta(seconds=5))
    )
    assert report.attempted == 0
    assert report.results == ()


def test_observation_runner_rejects_stale_market() -> None:
    with pytest.raises(PermissionError, match="stale"):
        ObservationOnlyRunner().run_cycle(
            CurrentMarketInput("XAUUSD", datetime.now(UTC) - timedelta(hours=1))
        )
