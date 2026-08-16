"""Validated experiment configuration and deterministic reproducibility manifest."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from quantora_trade.backtesting.splits import ChronologicalDatasetSplit

_SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


def _duration_seconds(value: timedelta) -> str:
    return str(
        Decimal(value.days * 86_400)
        + Decimal(value.seconds)
        + Decimal(value.microseconds) / Decimal("1000000")
    )


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """Complete immutable inputs required to identify one baseline experiment."""

    run_name: str
    code_commit_sha: str
    dirty_worktree: bool
    official: bool
    engine_version: str
    strategy_version: str
    risk_policy_version: str
    dataset_id: str
    dataset_sha256: str
    symbols: tuple[str, ...]
    timeframe: str
    period_start: datetime
    period_end: datetime
    initial_equity: Decimal
    account_currency: str
    cost_scenario: str
    random_seed: int

    def __post_init__(self) -> None:
        for field_name in (
            "run_name",
            "engine_version",
            "strategy_version",
            "risk_policy_version",
            "dataset_id",
            "cost_scenario",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be empty")
        if not _SHA1_PATTERN.fullmatch(self.code_commit_sha):
            raise ValueError("code_commit_sha must be a lowercase 40-character SHA-1")
        if not _SHA256_PATTERN.fullmatch(self.dataset_sha256):
            raise ValueError("dataset_sha256 must be a lowercase 64-character SHA-256")
        if self.official and self.dirty_worktree:
            raise ValueError("official experiment requires a clean worktree")
        if not self.symbols or self.symbols != tuple(sorted(set(self.symbols))):
            raise ValueError("symbols must be non-empty, unique, and sorted")
        if any(not symbol.strip() or symbol != symbol.strip().upper() for symbol in self.symbols):
            raise ValueError("symbols must use canonical uppercase names")
        if self.timeframe not in {"M5", "M15", "H1"}:
            raise ValueError("experiment timeframe is not supported")
        _require_utc(self.period_start, "period_start")
        _require_utc(self.period_end, "period_end")
        if self.period_start >= self.period_end:
            raise ValueError("experiment period must have positive duration")
        if not self.initial_equity.is_finite() or self.initial_equity <= 0:
            raise ValueError("initial equity must be finite and greater than zero")
        if (
            len(self.account_currency) != 3
            or not self.account_currency.isalpha()
            or self.account_currency != self.account_currency.upper()
        ):
            raise ValueError("account currency must be a canonical three-letter code")
        if self.random_seed < 0:
            raise ValueError("random seed must be non-negative")

    def to_record(self) -> dict[str, Any]:
        """Return a canonical JSON-safe experiment snapshot."""

        return {
            "run_name": self.run_name,
            "code_commit_sha": self.code_commit_sha,
            "dirty_worktree": self.dirty_worktree,
            "official": self.official,
            "engine_version": self.engine_version,
            "strategy_version": self.strategy_version,
            "risk_policy_version": self.risk_policy_version,
            "dataset_id": self.dataset_id,
            "dataset_sha256": self.dataset_sha256,
            "symbols": list(self.symbols),
            "timeframe": self.timeframe,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "initial_equity": str(self.initial_equity),
            "account_currency": self.account_currency,
            "cost_scenario": self.cost_scenario,
            "random_seed": self.random_seed,
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_record()).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReproducibilityManifest:
    """Stable identity tying code, config, dataset, and split membership together."""

    run_id: UUID
    config_sha256: str
    split_sha256: str
    config: ExperimentConfig
    split_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if self.config_sha256 != self.config.sha256:
            raise ValueError("manifest config checksum does not match config")
        if not _SHA256_PATTERN.fullmatch(self.split_sha256):
            raise ValueError("manifest split checksum must be a lowercase SHA-256")
        expected_names = ("excluded", "test", "training", "validation")
        if tuple(name for name, _ in self.split_counts) != expected_names:
            raise ValueError("manifest split counts must use canonical partition order")
        if any(count < 0 for _, count in self.split_counts):
            raise ValueError("manifest split counts must be non-negative")
        identity = _canonical_json(
            {"config_sha256": self.config_sha256, "split_sha256": self.split_sha256}
        )
        if self.run_id != uuid5(NAMESPACE_URL, identity):
            raise ValueError("manifest run ID does not match its checksums")

    def to_record(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "config_sha256": self.config_sha256,
            "split_sha256": self.split_sha256,
            "config": self.config.to_record(),
            "split_counts": dict(self.split_counts),
        }


def build_reproducibility_manifest(
    *, config: ExperimentConfig, split: ChronologicalDatasetSplit
) -> ReproducibilityManifest:
    """Build the same run identity for identical config and split membership."""

    split_record = {
        "training": [sample.id for sample in split.training],
        "validation": [sample.id for sample in split.validation],
        "test": [sample.id for sample in split.test],
        "excluded": [sample.id for sample in split.excluded],
        "validation_boundary": split.validation_boundary.isoformat(),
        "test_boundary": split.test_boundary.isoformat(),
        "purge_seconds": _duration_seconds(split.purge),
        "embargo_seconds": _duration_seconds(split.embargo),
    }
    split_sha256 = hashlib.sha256(_canonical_json(split_record).encode()).hexdigest()
    identity = _canonical_json({"config_sha256": config.sha256, "split_sha256": split_sha256})
    return ReproducibilityManifest(
        run_id=uuid5(NAMESPACE_URL, identity),
        config_sha256=config.sha256,
        split_sha256=split_sha256,
        config=config,
        split_counts=(
            ("excluded", len(split.excluded)),
            ("test", len(split.test)),
            ("training", len(split.training)),
            ("validation", len(split.validation)),
        ),
    )
