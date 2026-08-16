"""Deterministic historical simulation primitives."""

from quantora_trade.backtesting.broker import (
    BrokerFillDecision,
    BrokerSimulationModel,
    FillReason,
    FillStatus,
    calculate_swap_cost,
    simulate_broker_fill_decision,
)
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
    PersistedArtifacts,
    SymbolMetrics,
    build_baseline_artifacts,
    build_baseline_report,
    persist_baseline_artifacts,
    render_baseline_report_html,
)
from quantora_trade.backtesting.runner import (
    CompletedExperiment,
    ReplayEvent,
    ReplayFillDecision,
    ReplayProtectiveExit,
    ScheduledOrder,
    run_experiment,
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
    "BrokerFillDecision",
    "BrokerSimulationModel",
    "CandleEvent",
    "ChronologicalDatasetSplit",
    "ClosedTrade",
    "CompletedExperiment",
    "ExecutionCostModel",
    "ExperimentConfig",
    "FillReason",
    "FillStatus",
    "IntrabarExit",
    "IntrabarExitReason",
    "OpenPosition",
    "PartitionMetrics",
    "PartitionName",
    "PendingOrder",
    "PerformanceMetrics",
    "PersistedArtifacts",
    "PortfolioState",
    "ReplayEvent",
    "ReplayFillDecision",
    "ReplayProtectiveExit",
    "ReproducibilityManifest",
    "ScheduledOrder",
    "SimulatedFill",
    "SimulationClock",
    "SymbolMetrics",
    "TemporalSample",
    "TradeJournal",
    "build_baseline_artifacts",
    "build_baseline_report",
    "build_reproducibility_manifest",
    "calculate_performance_metrics",
    "calculate_swap_cost",
    "chronological_split",
    "persist_baseline_artifacts",
    "render_baseline_report_html",
    "run_experiment",
    "simulate_broker_fill_decision",
    "simulate_conservative_intrabar_exit",
    "simulate_next_bar_market_fill",
]
