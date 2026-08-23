"""Deterministic, read-only evaluation of an explicitly run PAPER soak.

This module consumes already captured observations.  It has no scheduler,
network client, broker port, worker control, or order-submission capability.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import pairwise
from math import ceil
from pathlib import Path
from typing import Any

from quantora_trade.domain.enums import TradingMode


def _utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")


def _text(value: str, name: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed value")


class SoakVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class PaperSoakManifest:
    run_id: str
    mode: TradingMode
    owner: str
    started_at: datetime
    target_duration_seconds: int
    sample_interval_seconds: int
    config_version: str
    config_sha256: str
    code_version: str
    data_version: str

    def __post_init__(self) -> None:
        for field in (
            "run_id",
            "owner",
            "config_version",
            "config_sha256",
            "code_version",
            "data_version",
        ):
            _text(str(getattr(self, field)), field)
        if self.mode is not TradingMode.PAPER:
            raise PermissionError("soak validation accepts PAPER mode only; LIVE is hard rejected")
        _utc(self.started_at, "started_at")
        if self.target_duration_seconds <= 0:
            raise ValueError("target_duration_seconds must be positive and owner-set")
        if self.sample_interval_seconds <= 0:
            raise ValueError("sample_interval_seconds must be positive")
        if self.target_duration_seconds < self.sample_interval_seconds:
            raise ValueError("target duration must span at least one sample interval")
        if self.target_duration_seconds % self.sample_interval_seconds:
            raise ValueError("target duration must be an exact multiple of the sample interval")
        if len(self.config_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.config_sha256
        ):
            raise ValueError("config_sha256 must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class SoakSample:
    observed_at: datetime
    health_ready: bool
    orders_seen: int
    duplicate_orders: int
    unknown_orders: int
    audited_orders: int
    critical_events: int

    def __post_init__(self) -> None:
        _utc(self.observed_at, "observed_at")
        for field in (
            "orders_seen",
            "duplicate_orders",
            "unknown_orders",
            "audited_orders",
            "critical_events",
        ):
            if getattr(self, field) < 0:
                raise ValueError(f"{field} must be nonnegative")
        if self.audited_orders > self.orders_seen:
            raise ValueError("audited_orders cannot exceed orders_seen")


@dataclass(frozen=True, slots=True)
class SoakIncident:
    occurred_at: datetime
    severity: str
    code: str
    summary: str

    def __post_init__(self) -> None:
        _utc(self.occurred_at, "occurred_at")
        for field in ("severity", "code", "summary"):
            _text(str(getattr(self, field)), field)


@dataclass(frozen=True, slots=True)
class PaperSoakGates:
    max_unhealthy_samples: int = 0
    max_duplicate_orders: int = 0
    max_unknown_orders: int = 0
    max_critical_incidents: int = 0
    require_complete_audit: bool = True

    def __post_init__(self) -> None:
        for field in (
            "max_unhealthy_samples",
            "max_duplicate_orders",
            "max_unknown_orders",
            "max_critical_incidents",
        ):
            if getattr(self, field) < 0:
                raise ValueError(f"{field} must be nonnegative")


@dataclass(frozen=True, slots=True)
class GateResult:
    code: str
    passed: bool
    actual: str
    expected: str


@dataclass(frozen=True, slots=True)
class PaperSoakReport:
    manifest: PaperSoakManifest
    completed_at: datetime
    source_sha256: str
    sample_count: int
    incident_count: int
    gates: tuple[GateResult, ...]
    verdict: SoakVerdict

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["manifest"]["mode"] = self.manifest.mode.value
        value["manifest"]["started_at"] = self.manifest.started_at.isoformat()
        value["completed_at"] = self.completed_at.isoformat()
        value["verdict"] = self.verdict.value
        return value

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        rows = "\n".join(
            f"| `{gate.code}` | {'PASS' if gate.passed else 'FAIL'} | "
            f"{gate.actual} | {gate.expected} |"
            for gate in self.gates
        )
        return (
            f"# PAPER Soak Audit — {self.manifest.run_id}\n\n"
            f"**Verdict:** `{self.verdict.value.upper()}`  \n"
            f"**Mode:** `PAPER`  \n"
            f"**Owner:** {self.manifest.owner}  \n"
            f"**Observed:** {self.manifest.started_at.isoformat()} to "
            f"{self.completed_at.isoformat()}  \n"
            f"**Samples / incidents:** {self.sample_count} / {self.incident_count}  \n"
            f"**Source SHA-256:** `{self.source_sha256}`\n\n"
            "| Gate | Result | Actual | Expected |\n|---|---:|---:|---|\n"
            f"{rows}\n\n"
            "> A PASS is evidence for this bounded PAPER run only. "
            "It does not authorize LIVE trading.\n"
        )


def evaluate_paper_soak(
    *,
    manifest: PaperSoakManifest,
    samples: tuple[SoakSample, ...],
    incidents: tuple[SoakIncident, ...],
    gates: PaperSoakGates,
) -> PaperSoakReport:
    """Evaluate sorted cumulative observations without mutating runtime state."""

    if not samples:
        raise ValueError("at least one soak sample is required")
    if samples != tuple(sorted(samples, key=lambda item: item.observed_at)):
        raise ValueError("samples must be chronologically sorted")
    if any(current.observed_at <= previous.observed_at for previous, current in pairwise(samples)):
        raise ValueError("sample timestamps must be unique and strictly increasing")
    if incidents != tuple(sorted(incidents, key=lambda item: item.occurred_at)):
        raise ValueError("incidents must be chronologically sorted")
    if samples[0].observed_at < manifest.started_at:
        raise ValueError("samples cannot predate manifest.started_at")
    cumulative_fields = (
        "orders_seen",
        "duplicate_orders",
        "unknown_orders",
        "audited_orders",
        "critical_events",
    )
    if any(
        getattr(current, field) < getattr(previous, field)
        for previous, current in pairwise(samples)
        for field in cumulative_fields
    ):
        raise ValueError("cumulative metrics must never decrease")

    completed_at = samples[-1].observed_at
    if any(
        item.occurred_at < manifest.started_at or item.occurred_at > completed_at
        for item in incidents
    ):
        raise ValueError("incidents must fall within the observed run")
    elapsed = int((completed_at - manifest.started_at).total_seconds())
    latest = samples[-1]
    unhealthy = sum(not sample.health_ready for sample in samples)
    critical_incidents = sum(item.severity.lower() == "critical" for item in incidents)
    critical_total = latest.critical_events + critical_incidents
    audit_complete = latest.audited_orders == latest.orders_seen
    interval = manifest.sample_interval_seconds
    # The interval is owner-set before the run.  A bounded 10% scheduling tolerance
    # (at least one second) allows ordinary collector jitter without allowing a
    # clustered batch to masquerade as continuous observation.
    cadence_tolerance = max(1, interval // 10)
    target_end = manifest.started_at.timestamp() + manifest.target_duration_seconds
    first_offset = int((samples[0].observed_at - manifest.started_at).total_seconds())
    last_offset = int(samples[-1].observed_at.timestamp() - target_end)
    gaps = tuple(
        int((current.observed_at - previous.observed_at).total_seconds())
        for previous, current in pairwise(samples)
    )
    minimum_gap = max(1, interval - cadence_tolerance)
    maximum_gap = interval + cadence_tolerance
    cadence_ok = all(minimum_gap <= gap <= maximum_gap for gap in gaps)
    minimum_samples = ceil(manifest.target_duration_seconds / interval) + 1
    results = (
        GateResult(
            "duration",
            elapsed >= manifest.target_duration_seconds,
            str(elapsed),
            f">={manifest.target_duration_seconds} seconds",
        ),
        GateResult(
            "sample_count",
            len(samples) >= minimum_samples,
            str(len(samples)),
            f">={minimum_samples}",
        ),
        GateResult(
            "first_sample_at_start",
            0 <= first_offset <= cadence_tolerance,
            f"{first_offset} seconds after start",
            f"between 0 and {cadence_tolerance} seconds after start",
        ),
        GateResult(
            "sample_cadence",
            cadence_ok,
            f"gaps={list(gaps)} seconds",
            f"every gap between {minimum_gap} and {maximum_gap} seconds",
        ),
        GateResult(
            "last_sample_at_end",
            -cadence_tolerance <= last_offset <= cadence_tolerance,
            f"{last_offset:+d} seconds from target end",
            f"within +/-{cadence_tolerance} seconds of target end",
        ),
        GateResult(
            "unhealthy_samples",
            unhealthy <= gates.max_unhealthy_samples,
            str(unhealthy),
            f"<={gates.max_unhealthy_samples}",
        ),
        GateResult(
            "duplicate_orders",
            latest.duplicate_orders <= gates.max_duplicate_orders,
            str(latest.duplicate_orders),
            f"<={gates.max_duplicate_orders}",
        ),
        GateResult(
            "unknown_orders",
            latest.unknown_orders <= gates.max_unknown_orders,
            str(latest.unknown_orders),
            f"<={gates.max_unknown_orders}",
        ),
        GateResult(
            "critical_incidents",
            critical_total <= gates.max_critical_incidents,
            str(critical_total),
            f"<={gates.max_critical_incidents}",
        ),
        GateResult(
            "audit_complete",
            audit_complete or not gates.require_complete_audit,
            f"{latest.audited_orders}/{latest.orders_seen}",
            "all orders audited" if gates.require_complete_audit else "not required",
        ),
    )
    source = {
        "incidents": [_jsonable(asdict(item)) for item in incidents],
        "manifest": _jsonable(asdict(manifest)),
        "samples": [_jsonable(asdict(item)) for item in samples],
    }
    digest = hashlib.sha256(
        json.dumps(source, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    verdict = SoakVerdict.PASS if all(item.passed for item in results) else SoakVerdict.FAIL
    return PaperSoakReport(
        manifest=manifest,
        completed_at=completed_at,
        source_sha256=digest,
        sample_count=len(samples),
        incident_count=len(incidents),
        gates=results,
        verdict=verdict,
    )


def write_report(report: PaperSoakReport, output_directory: Path) -> tuple[Path, Path]:
    """Persist immutable report formats; refuse to overwrite prior evidence."""

    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / f"{report.manifest.run_id}.json"
    markdown_path = output_directory / f"{report.manifest.run_id}.md"
    for path in (json_path, markdown_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite audit artifact: {path}")
    json_path.write_text(report.to_json(), encoding="utf-8")
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")
    return json_path, markdown_path


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
