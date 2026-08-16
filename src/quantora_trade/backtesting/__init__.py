"""Deterministic historical simulation primitives."""

from quantora_trade.backtesting.clock import CandleEvent, SimulationClock
from quantora_trade.backtesting.engine import BacktestEngine, BacktestStep, PendingOrder
from quantora_trade.backtesting.execution import (
    ExecutionCostModel,
    SimulatedFill,
    simulate_next_bar_market_fill,
)
from quantora_trade.backtesting.intrabar import (
    IntrabarExit,
    IntrabarExitReason,
    simulate_conservative_intrabar_exit,
)
from quantora_trade.backtesting.journal import TradeJournal
from quantora_trade.backtesting.metrics import PerformanceMetrics, calculate_performance_metrics
from quantora_trade.backtesting.portfolio import ClosedTrade, OpenPosition, PortfolioState
from quantora_trade.backtesting.splits import (
    ChronologicalDatasetSplit,
    TemporalSample,
    chronological_split,
)

__all__ = [
    "BacktestEngine",
    "BacktestStep",
    "CandleEvent",
    "ChronologicalDatasetSplit",
    "ClosedTrade",
    "ExecutionCostModel",
    "IntrabarExit",
    "IntrabarExitReason",
    "OpenPosition",
    "PendingOrder",
    "PerformanceMetrics",
    "PortfolioState",
    "SimulatedFill",
    "SimulationClock",
    "TemporalSample",
    "TradeJournal",
    "calculate_performance_metrics",
    "chronological_split",
    "simulate_conservative_intrabar_exit",
    "simulate_next_bar_market_fill",
]
