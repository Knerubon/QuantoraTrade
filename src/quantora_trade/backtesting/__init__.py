"""Deterministic historical simulation primitives."""

from quantora_trade.backtesting.clock import CandleEvent, SimulationClock
from quantora_trade.backtesting.engine import BacktestEngine, BacktestStep, PendingOrder
from quantora_trade.backtesting.execution import (
    ExecutionCostModel,
    SimulatedFill,
    simulate_next_bar_market_fill,
)
from quantora_trade.backtesting.experiment import (
    ExperimentConfig,
    ReproducibilityManifest,
    build_reproducibility_manifest,
)
from quantora_trade.backtesting.intrabar import (
    IntrabarExit,
    IntrabarExitReason,
    simulate_conservative_intrabar_exit,
)
from quantora_trade.backtesting.journal import TradeJournal
from quantora_trade.backtesting.metrics import PerformanceMetrics, calculate_performance_metrics
from quantora_trade.backtesting.portfolio import ClosedTrade, OpenPosition, PortfolioState
from quantora_trade.backtesting.reporting import (
    Artifact,
    BaselineArtifacts,
    BaselineReport,
    PartitionMetrics,
    PartitionName,
    SymbolMetrics,
    build_baseline_artifacts,
    build_baseline_report,
)
from quantora_trade.backtesting.splits import (
    ChronologicalDatasetSplit,
    TemporalSample,
    chronological_split,
)

__all__ = [
    "Artifact",
    "BacktestEngine",
    "BacktestStep",
    "BaselineArtifacts",
    "BaselineReport",
    "CandleEvent",
    "ChronologicalDatasetSplit",
    "ClosedTrade",
    "ExecutionCostModel",
    "ExperimentConfig",
    "IntrabarExit",
    "IntrabarExitReason",
    "OpenPosition",
    "PartitionMetrics",
    "PartitionName",
    "PendingOrder",
    "PerformanceMetrics",
    "PortfolioState",
    "ReproducibilityManifest",
    "SimulatedFill",
    "SimulationClock",
    "SymbolMetrics",
    "TemporalSample",
    "TradeJournal",
    "build_baseline_artifacts",
    "build_baseline_report",
    "build_reproducibility_manifest",
    "calculate_performance_metrics",
    "chronological_split",
    "simulate_conservative_intrabar_exit",
    "simulate_next_bar_market_fill",
]
