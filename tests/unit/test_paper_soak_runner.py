import json
from pathlib import Path
from typing import Any

import pytest

from quantora_trade.validation.paper_soak_runner import PaperSoakRunner, RunnerSettings


class FakeApi:
    def __init__(self, *, mode: str = "paper", fail_stop: bool = False) -> None:
        self.mode = mode
        self.fail_stop = fail_stop
        self.actions: list[str] = []

    def get(self, path: str) -> dict[str, Any]:
        if path == "/api/v1/status":
            return {"mode": self.mode, "ready": True}
        if path == "/api/v1/dashboard":
            return {
                "mode": self.mode,
                "worker": {"state": "healthy"},
                "dependencies": [{"component": "database", "state": "healthy"}],
                "degraded_reason_codes": [],
                "orders": [
                    {
                        "order_id": "order-1",
                        "symbol": "XAUUSD",
                        "side": "BUY",
                        "quantity": "0.01",
                        "filled_quantity": "0.01",
                        "status": "filled",
                        "created_at": "2026-08-23T00:00:00+00:00",
                    }
                ],
                "fills": [{"fill_id": "fill-1", "order_id": "order-1"}],
            }
        if path.startswith("/api/v1/events"):
            return {"events": [], "next_cursor": 0}
        if path.startswith("/api/v1/system/commands/"):
            return {"status": "succeeded"}
        raise AssertionError(path)

    def post(self, path: str, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        assert payload["mode"] == "paper"
        assert idempotency_key.startswith("paper-soak:")
        action = path.rsplit("/", maxsplit=1)[-1]
        self.actions.append(action)
        if action == "stop" and self.fail_stop:
            raise RuntimeError("stop unavailable")
        return {"id": f"command-{action}"}


def settings(tmp_path: Path) -> RunnerSettings:
    config = tmp_path / "paper.yaml"
    config.write_text("mode: paper\n", encoding="utf-8")
    return RunnerSettings(
        owner="เจ้านาย",
        run_id="paper-test-1",
        duration_seconds=2,
        interval_seconds=1,
        symbols=("XAUUSD",),
        strategy_id="technical-v1",
        config_version="paper-v1",
        config_path=config,
        data_version="mt5-demo-current",
        code_version="abc123",
        evidence_path=tmp_path / "observations.json",
    )


def test_runner_starts_samples_stops_and_persists_atomic_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("quantora_trade.validation.paper_soak_runner.time.sleep", lambda _: None)
    monkeypatch.setattr("quantora_trade.validation.paper_soak_runner.time.monotonic", lambda: 0.0)
    api = FakeApi()
    path = PaperSoakRunner(api, settings(tmp_path)).run()

    evidence = json.loads(path.read_text(encoding="utf-8"))
    assert api.actions == ["start", "stop"]
    assert evidence["manifest"]["mode"] == "paper"
    assert len(evidence["samples"]) == 3
    assert evidence["samples"][-1]["orders_seen"] == 1
    assert evidence["samples"][-1]["audited_orders"] == 1
    assert evidence["samples"][-1]["duplicate_orders"] == 0
    assert not path.with_suffix(".json.tmp").exists()


def test_runner_hard_rejects_non_paper_before_control(tmp_path: Path) -> None:
    api = FakeApi(mode="live")
    with pytest.raises(PermissionError, match="LIVE is rejected"):
        PaperSoakRunner(api, settings(tmp_path)).run()
    assert api.actions == []


def test_stop_failure_is_recorded_without_hiding_original_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("quantora_trade.validation.paper_soak_runner.time.sleep", lambda _: None)
    monkeypatch.setattr("quantora_trade.validation.paper_soak_runner.time.monotonic", lambda: 0.0)
    value = settings(tmp_path)
    PaperSoakRunner(FakeApi(fail_stop=True), value).run()

    evidence = json.loads(value.evidence_path.read_text(encoding="utf-8"))
    assert evidence["incidents"][-1]["code"] == "CONTROLLED_STOP_FAILURE"
    assert evidence["incidents"][-1]["occurred_at"] == evidence["samples"][-1]["observed_at"]


def test_settings_refuse_overwrite_and_invalid_cadence(tmp_path: Path) -> None:
    value = settings(tmp_path)
    value.evidence_path.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        settings(tmp_path)
    value.evidence_path.unlink()
    with pytest.raises(ValueError, match="exact multiple"):
        RunnerSettings(
            owner=value.owner,
            run_id=value.run_id,
            duration_seconds=3,
            interval_seconds=2,
            symbols=value.symbols,
            strategy_id=value.strategy_id,
            config_version=value.config_version,
            config_path=value.config_path,
            data_version=value.data_version,
            code_version=value.code_version,
            evidence_path=value.evidence_path,
        )
