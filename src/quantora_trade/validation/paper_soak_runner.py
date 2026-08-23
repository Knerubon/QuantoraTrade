"""Bounded PAPER-only soak orchestration for an already running control plane."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4


class PaperApi(Protocol):
    def get(self, path: str) -> dict[str, Any]: ...

    def post(
        self, path: str, payload: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]: ...


class ControlPlaneClient:
    """Small stdlib HTTP client which never logs or persists its bearer token."""

    def __init__(self, base_url: str, token: str, timeout_seconds: int = 15) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("api_url must start with http:// or https://")
        if not token:
            raise ValueError("bearer token is required")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path, None, {})

    def post(self, path: str, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        return self._request(
            "POST",
            path,
            payload,
            {"Idempotency-Key": idempotency_key, "X-Request-ID": f"soak-{uuid4().hex}"},
        )

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None, headers: dict[str, str]
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                **headers,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                value = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            raise RuntimeError(f"control plane returned HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"control plane unavailable: {error.reason}") from error
        if not isinstance(value, dict):
            raise RuntimeError("control plane returned a non-object response")
        return value


@dataclass(frozen=True, slots=True)
class RunnerSettings:
    owner: str
    run_id: str
    duration_seconds: int
    interval_seconds: int
    symbols: tuple[str, ...]
    strategy_id: str
    config_version: str
    config_path: Path
    data_version: str
    code_version: str
    evidence_path: Path
    command_timeout_seconds: int = 120

    def __post_init__(self) -> None:
        for name in (
            "owner",
            "run_id",
            "strategy_id",
            "config_version",
            "data_version",
            "code_version",
        ):
            value = getattr(self, name)
            if not value or value != value.strip():
                raise ValueError(f"{name} must be a non-empty trimmed value")
        if self.duration_seconds <= 0 or self.interval_seconds <= 0:
            raise ValueError("duration and interval must be positive")
        if self.duration_seconds < self.interval_seconds:
            raise ValueError("duration must span at least one interval")
        if self.duration_seconds % self.interval_seconds:
            raise ValueError("duration must be an exact multiple of interval")
        if not self.symbols or any(symbol != symbol.strip().upper() for symbol in self.symbols):
            raise ValueError("symbols must be canonical uppercase values")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be unique")
        if not self.config_path.is_file():
            raise ValueError("config_path must identify an existing file")
        if self.evidence_path.exists():
            raise FileExistsError(f"refusing to overwrite evidence: {self.evidence_path}")


@dataclass(slots=True)
class ObservationState:
    orders: dict[str, str] = field(default_factory=dict)
    fills: set[str] = field(default_factory=set)
    duplicate_orders: int = 0
    unknown_orders: set[str] = field(default_factory=set)
    critical_events: int = 0
    event_cursor: int = 0


class PaperSoakRunner:
    """Start, observe and stop one bounded PAPER workload; LIVE is never accepted."""

    def __init__(self, api: PaperApi, settings: RunnerSettings) -> None:
        self._api = api
        self._settings = settings
        self._state = ObservationState()
        self._evidence: dict[str, Any] | None = None

    def run(self) -> Path:
        self._preflight()
        self._evidence = self._new_evidence()
        self._write_evidence()
        started = False
        try:
            command = self._control("start")
            started = True
            self._wait_for_command(str(command["id"]))
            self._evidence["manifest"]["started_at"] = datetime.now(UTC).isoformat()
            self._write_evidence()
            start_monotonic = time.monotonic()
            sample_count = self._settings.duration_seconds // self._settings.interval_seconds + 1
            for index in range(sample_count):
                deadline = start_monotonic + index * self._settings.interval_seconds
                delay = deadline - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                self._capture_sample()
            return self._settings.evidence_path
        except Exception as error:
            self._record_incident("critical", "SOAK_RUNNER_FAILURE", str(error)[:300])
            raise
        finally:
            if started:
                try:
                    command = self._control("stop")
                    self._wait_for_command(str(command["id"]))
                except Exception as error:
                    self._record_incident("critical", "CONTROLLED_STOP_FAILURE", str(error)[:300])
            self._write_evidence()

    def _preflight(self) -> None:
        status = self._api.get("/api/v1/status")
        if str(status.get("mode", "")).lower() != "paper":
            raise PermissionError(
                "refusing soak: control plane mode must be PAPER; LIVE is rejected"
            )

    def _new_evidence(self) -> dict[str, Any]:
        config_digest = hashlib.sha256(self._settings.config_path.read_bytes()).hexdigest()
        return {
            "gates": {
                "max_critical_incidents": 0,
                "max_duplicate_orders": 0,
                "max_unhealthy_samples": 0,
                "max_unknown_orders": 0,
                "require_complete_audit": True,
            },
            "incidents": [],
            "manifest": {
                "code_version": self._settings.code_version,
                "config_sha256": config_digest,
                "config_version": self._settings.config_version,
                "data_version": self._settings.data_version,
                "mode": "paper",
                "owner": self._settings.owner,
                "run_id": self._settings.run_id,
                "sample_interval_seconds": self._settings.interval_seconds,
                "started_at": datetime.now(UTC).isoformat(),
                "target_duration_seconds": self._settings.duration_seconds,
            },
            "samples": [],
        }

    def _control(self, action: str) -> dict[str, Any]:
        payload = {
            "mode": "paper",
            "symbols": list(self._settings.symbols),
            "strategy_id": self._settings.strategy_id,
            "reason": f"owner-approved PAPER soak {self._settings.run_id}: {action}",
        }
        return self._api.post(
            f"/api/v1/system/{action}",
            payload,
            idempotency_key=f"paper-soak:{self._settings.run_id}:{action}",
        )

    def _wait_for_command(self, command_id: str) -> None:
        deadline = time.monotonic() + self._settings.command_timeout_seconds
        while time.monotonic() < deadline:
            command = self._api.get(f"/api/v1/system/commands/{command_id}")
            status = command.get("status")
            if status == "succeeded":
                return
            if status == "failed":
                raise RuntimeError(f"PAPER command failed: {command_id}")
            time.sleep(1)
        raise TimeoutError(f"PAPER command timed out: {command_id}")

    def _capture_sample(self) -> None:
        assert self._evidence is not None
        status = self._api.get("/api/v1/status")
        dashboard = self._api.get("/api/v1/dashboard")
        if (
            str(status.get("mode", "")).lower() != "paper"
            or str(dashboard.get("mode", "")).lower() != "paper"
        ):
            raise PermissionError("mode changed away from PAPER during soak")
        self._consume_orders(dashboard.get("orders", []))
        self._consume_fills(dashboard.get("fills", []))
        self._consume_events()
        dependencies = dashboard.get("dependencies", [])
        ready = bool(status.get("ready")) and dashboard.get("worker", {}).get("state") == "healthy"
        ready = ready and all(item.get("state") == "healthy" for item in dependencies)
        ready = ready and not dashboard.get("degraded_reason_codes", [])
        self._evidence["samples"].append(
            {
                "audited_orders": len(self._state.orders),
                "critical_events": self._state.critical_events,
                "duplicate_orders": self._state.duplicate_orders,
                "health_ready": ready,
                "observed_at": datetime.now(UTC).isoformat(),
                "orders_seen": len(self._state.orders),
                "unknown_orders": len(self._state.unknown_orders),
            }
        )
        self._write_evidence()

    def _consume_orders(self, raw_orders: Any) -> None:
        if not isinstance(raw_orders, list):
            raise RuntimeError("dashboard orders must be an array")
        seen_snapshot: set[str] = set()
        for order in raw_orders:
            order_id = str(order["order_id"])
            immutable = {
                key: order.get(key)
                for key in ("order_id", "symbol", "side", "quantity", "created_at")
            }
            fingerprint = hashlib.sha256(
                json.dumps(immutable, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if order_id in seen_snapshot:
                self._state.duplicate_orders += 1
            seen_snapshot.add(order_id)
            previous = self._state.orders.get(order_id)
            if previous is not None and previous != fingerprint:
                self._state.duplicate_orders += 1
            self._state.orders[order_id] = fingerprint

    def _consume_fills(self, raw_fills: Any) -> None:
        if not isinstance(raw_fills, list):
            raise RuntimeError("dashboard fills must be an array")
        for fill in raw_fills:
            self._state.fills.add(str(fill["fill_id"]))
            order_id = str(fill["order_id"])
            if order_id not in self._state.orders:
                self._state.unknown_orders.add(order_id)

    def _consume_events(self) -> None:
        page = self._api.get(f"/api/v1/events?cursor={self._state.event_cursor}&limit=500")
        events = page.get("events", [])
        if not isinstance(events, list):
            raise RuntimeError("dashboard events must be an array")
        for event in events:
            reason = str(event.get("reason_code", "")).upper()
            if reason.startswith(("CRITICAL_", "SECURITY_")):
                self._state.critical_events += 1
        self._state.event_cursor = int(page.get("next_cursor", self._state.event_cursor))

    def _record_incident(self, severity: str, code: str, summary: str) -> None:
        if self._evidence is None:
            return
        samples = self._evidence["samples"]
        occurred_at = (
            samples[-1]["observed_at"] if samples else self._evidence["manifest"]["started_at"]
        )
        self._evidence["incidents"].append(
            {
                "occurred_at": occurred_at,
                "severity": severity,
                "code": code,
                "summary": summary,
            }
        )

    def _write_evidence(self) -> None:
        if self._evidence is None:
            return
        path = self._settings.evidence_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)


__all__ = ["ControlPlaneClient", "PaperSoakRunner", "RunnerSettings"]
