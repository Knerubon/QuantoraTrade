"""Canonical baseline reports and checksummed in-memory artifact bundles."""

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from quantora_trade.backtesting.experiment import (
    ExperimentConfig,
    ReproducibilityManifest,
    build_reproducibility_manifest,
)
from quantora_trade.backtesting.journal import TradeJournal
from quantora_trade.backtesting.metrics import PerformanceMetrics, calculate_performance_metrics
from quantora_trade.backtesting.splits import ChronologicalDatasetSplit


class PartitionName(StrEnum):
    TRAINING = "training"
    VALIDATION = "validation"
    TEST = "test"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode()


@dataclass(frozen=True, slots=True)
class PartitionMetrics:
    name: PartitionName
    metrics: PerformanceMetrics


@dataclass(frozen=True, slots=True)
class SymbolMetrics:
    symbol: str
    metrics: PerformanceMetrics

    def __post_init__(self) -> None:
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("report symbol must be canonical uppercase")


@dataclass(frozen=True, slots=True)
class BaselineReport:
    """Machine-readable comparison with a no-trade baseline."""

    schema_version: str
    manifest: ReproducibilityManifest
    overall: PerformanceMetrics
    partitions: tuple[PartitionMetrics, ...]
    symbols: tuple[SymbolMetrics, ...]
    no_trade_net_pnl: Decimal = Decimal("0")
    no_trade_total_return: Decimal = Decimal("0")
    promotion_decision: str = "RESEARCH_ONLY"

    def __post_init__(self) -> None:
        if not self.schema_version.strip():
            raise ValueError("report schema version must not be empty")
        if tuple(item.name for item in self.partitions) != tuple(PartitionName):
            raise ValueError("report partitions must use canonical order")
        if tuple(item.symbol for item in self.symbols) != self.manifest.config.symbols:
            raise ValueError("report symbols must match experiment configuration")
        if self.no_trade_net_pnl != 0 or self.no_trade_total_return != 0:
            raise ValueError("no-trade baseline must remain zero")
        if self.promotion_decision != "RESEARCH_ONLY":
            raise ValueError("baseline report cannot authorize paper or live trading")

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest": self.manifest.to_record(),
            "overall": _json_safe(asdict(self.overall)),
            "partitions": [
                {"name": item.name.value, "metrics": _json_safe(asdict(item.metrics))}
                for item in self.partitions
            ],
            "symbols": [
                {"symbol": item.symbol, "metrics": _json_safe(asdict(item.metrics))}
                for item in self.symbols
            ],
            "no_trade": {
                "net_pnl": str(self.no_trade_net_pnl),
                "total_return": str(self.no_trade_total_return),
            },
            "promotion_decision": self.promotion_decision,
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_record())).hexdigest()


@dataclass(frozen=True, slots=True)
class Artifact:
    name: str
    content: bytes
    sha256: str

    def __post_init__(self) -> None:
        if not self.name.strip() or "/" in self.name or "\\" in self.name:
            raise ValueError("artifact name must be a simple file name")
        if hashlib.sha256(self.content).hexdigest() != self.sha256:
            raise ValueError("artifact checksum does not match content")


@dataclass(frozen=True, slots=True)
class BaselineArtifacts:
    files: tuple[Artifact, ...]

    def __post_init__(self) -> None:
        names = tuple(file.name for file in self.files)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("artifacts must have unique names in canonical order")

    def get(self, name: str) -> Artifact:
        matches = tuple(file for file in self.files if file.name == name)
        if not matches:
            raise KeyError(name)
        return matches[0]


def _combine_journals(journals: tuple[TradeJournal, ...]) -> TradeJournal:
    trades = tuple(
        sorted(
            (trade for journal in journals for trade in journal.trades),
            key=lambda trade: (trade.closed_at, str(trade.position_id)),
        )
    )
    return TradeJournal(trades=trades)


def build_baseline_report(
    *,
    config: ExperimentConfig,
    manifest: ReproducibilityManifest,
    split: ChronologicalDatasetSplit,
    training: TradeJournal,
    validation: TradeJournal,
    test: TradeJournal,
) -> tuple[BaselineReport, TradeJournal]:
    """Aggregate partition and symbol metrics without crossing time boundaries."""

    if manifest.config != config:
        raise ValueError("manifest does not belong to experiment configuration")
    if manifest != build_reproducibility_manifest(config=config, split=split):
        raise ValueError("manifest does not belong to dataset split")
    if any(trade.closed_at >= split.validation_boundary for trade in training.trades):
        raise ValueError("training trade closes outside the training period")
    if any(
        trade.closed_at < split.validation_boundary or trade.closed_at >= split.test_boundary
        for trade in validation.trades
    ):
        raise ValueError("validation trade closes outside the validation period")
    if any(trade.closed_at < split.test_boundary for trade in test.trades):
        raise ValueError("test trade closes outside the test period")

    journals = (training, validation, test)
    overall_journal = _combine_journals(journals)
    unknown_symbols = {trade.symbol for trade in overall_journal.trades} - set(config.symbols)
    if unknown_symbols:
        raise ValueError("trade journal contains a symbol outside experiment configuration")
    partitions = tuple(
        PartitionMetrics(
            name=name,
            metrics=calculate_performance_metrics(
                journal=journal, initial_equity=config.initial_equity
            ),
        )
        for name, journal in zip(PartitionName, journals, strict=True)
    )
    symbols = tuple(
        SymbolMetrics(
            symbol=symbol,
            metrics=calculate_performance_metrics(
                journal=overall_journal.filter(symbol=symbol),
                initial_equity=config.initial_equity,
            ),
        )
        for symbol in config.symbols
    )
    return (
        BaselineReport(
            schema_version="1.0.0",
            manifest=manifest,
            overall=calculate_performance_metrics(
                journal=overall_journal, initial_equity=config.initial_equity
            ),
            partitions=partitions,
            symbols=symbols,
        ),
        overall_journal,
    )


def build_baseline_artifacts(*, report: BaselineReport, journal: TradeJournal) -> BaselineArtifacts:
    """Build deterministic JSON artifacts with a separate checksum index."""

    expected_metrics = calculate_performance_metrics(
        journal=journal, initial_equity=report.manifest.config.initial_equity
    )
    if report.overall != expected_metrics:
        raise ValueError("artifact journal does not match baseline report")
    payloads = {
        "manifest.json": _canonical_json(report.manifest.to_record()),
        "summary.json": _canonical_json(report.to_record()),
        "trades.json": _canonical_json(journal.to_records()),
    }
    checksums = {name: hashlib.sha256(content).hexdigest() for name, content in payloads.items()}
    payloads["checksums.json"] = _canonical_json(checksums)
    return BaselineArtifacts(
        files=tuple(
            Artifact(name=name, content=content, sha256=hashlib.sha256(content).hexdigest())
            for name, content in sorted(payloads.items())
        )
    )
