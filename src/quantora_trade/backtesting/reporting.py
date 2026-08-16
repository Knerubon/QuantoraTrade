"""Canonical baseline reports and checksummed in-memory artifact bundles."""

import hashlib
import html
import json
import shutil
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
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


@dataclass(frozen=True, slots=True)
class PersistedArtifacts:
    directory: Path
    files: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.directory.is_absolute():
            raise ValueError("artifact directory must be absolute")
        names = tuple(name for name, _ in self.files)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("persisted artifacts must use canonical unique names")


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


def render_baseline_report_html(report: BaselineReport) -> bytes:
    """Render a deterministic, dependency-free HTML summary for human review."""

    def metric_rows(metrics: PerformanceMetrics) -> str:
        fields = (
            ("Net P&amp;L", metrics.net_pnl),
            ("Total return", metrics.total_return),
            ("Trades", metrics.trade_count),
            ("Win rate", metrics.win_rate),
            ("Expectancy", metrics.expectancy),
            ("Profit factor", metrics.profit_factor),
            ("Max drawdown", metrics.max_drawdown),
            ("Max drawdown rate", metrics.max_drawdown_rate),
        )
        return "".join(
            f"<tr><th>{name}</th><td>{html.escape(str(value))}</td></tr>" for name, value in fields
        )

    partition_sections = "".join(
        f"<h3>{html.escape(item.name.value.title())}</h3><table>{metric_rows(item.metrics)}</table>"
        for item in report.partitions
    )
    symbol_sections = "".join(
        f"<h3>{html.escape(item.symbol)}</h3><table>{metric_rows(item.metrics)}</table>"
        for item in report.symbols
    )
    config = report.manifest.config
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>QuantoraTrade Baseline Report</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:32px auto;"
        "padding:0 20px;color:#18212b}table{border-collapse:collapse;width:100%;margin:8px 0 24px}"
        "th,td{border:1px solid #d9e0e7;padding:8px;text-align:left}"
        "th{width:45%;background:#f5f7fa}"
        ".guard{padding:12px;background:#fff4d6;border:1px solid #e8bd52}</style></head><body>"
        "<h1>QuantoraTrade Baseline Report</h1>"
        f'<p class="guard">Promotion decision: {html.escape(report.promotion_decision)}</p>'
        f"<p>Run: {html.escape(str(report.manifest.run_id))}<br>"
        f"Dataset: {html.escape(config.dataset_id)}<br>"
        f"Strategy: {html.escape(config.strategy_version)}<br>"
        f"Period: {html.escape(config.period_start.isoformat())} — "
        f"{html.escape(config.period_end.isoformat())}</p>"
        f"<h2>Overall</h2><table>{metric_rows(report.overall)}</table>"
        f"<h2>Partitions</h2>{partition_sections}"
        f"<h2>Symbols</h2>{symbol_sections}"
        "<h2>No-trade baseline</h2><table>"
        f"<tr><th>Net P&amp;L</th><td>{report.no_trade_net_pnl}</td></tr>"
        f"<tr><th>Total return</th><td>{report.no_trade_total_return}</td></tr>"
        "</table></body></html>\n"
    )
    return document.encode()


def build_baseline_artifacts(
    *,
    report: BaselineReport,
    journal: TradeJournal,
    event_records: tuple[dict[str, Any], ...] = (),
) -> BaselineArtifacts:
    """Build deterministic JSON artifacts with a separate checksum index."""

    expected_metrics = calculate_performance_metrics(
        journal=journal, initial_equity=report.manifest.config.initial_equity
    )
    if report.overall != expected_metrics:
        raise ValueError("artifact journal does not match baseline report")
    payloads = {
        "events.json": _canonical_json(event_records),
        "manifest.json": _canonical_json(report.manifest.to_record()),
        "report.html": render_baseline_report_html(report),
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


def persist_baseline_artifacts(
    *, artifacts: BaselineArtifacts, output_directory: Path
) -> PersistedArtifacts:
    """Write a bundle through a staging directory, verify it, then publish atomically."""

    target = output_directory.resolve()
    if target.exists():
        raise FileExistsError(f"artifact directory already exists: {target}")
    if not target.parent.exists():
        raise FileNotFoundError(f"artifact parent directory does not exist: {target.parent}")
    bundle_identity = hashlib.sha256(
        "".join(file.sha256 for file in artifacts.files).encode()
    ).hexdigest()[:16]
    staging = target.with_name(f".{target.name}.{bundle_identity}.tmp")
    if staging.exists():
        raise FileExistsError(f"artifact staging directory already exists: {staging}")

    try:
        staging.mkdir()
        for artifact in artifacts.files:
            path = staging / artifact.name
            path.write_bytes(artifact.content)
            if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
                raise OSError(f"artifact verification failed: {artifact.name}")
        staging.rename(target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return PersistedArtifacts(
        directory=target,
        files=tuple((artifact.name, artifact.sha256) for artifact in artifacts.files),
    )
