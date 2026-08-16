"""Causal end-to-end runner for reproducible research experiments."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from quantora_trade.backtesting.engine import BacktestEngine, BacktestStep, PendingOrder
from quantora_trade.backtesting.experiment import (
    ExperimentConfig,
    ReproducibilityManifest,
    build_reproducibility_manifest,
)
from quantora_trade.backtesting.journal import TradeJournal
from quantora_trade.backtesting.portfolio import PortfolioState
from quantora_trade.backtesting.reporting import (
    BaselineArtifacts,
    BaselineReport,
    PartitionName,
    build_baseline_artifacts,
    build_baseline_report,
)
from quantora_trade.backtesting.splits import ChronologicalDatasetSplit, TemporalSample


@dataclass(frozen=True, slots=True)
class ReplayFillDecision:
    status: str
    requested_volume: str
    filled_volume: str
    remaining_volume: str
    margin_required: str
    reason_codes: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "requested_volume": self.requested_volume,
            "filled_volume": self.filled_volume,
            "remaining_volume": self.remaining_volume,
            "margin_required": self.margin_required,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class ReplayProtectiveExit:
    fill_id: UUID
    reason: str
    ambiguous: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "fill_id": str(self.fill_id),
            "reason": self.reason,
            "ambiguous": self.ambiguous,
        }


@dataclass(frozen=True, slots=True)
class ScheduledOrder:
    """One order tied to an included point-in-time sample and partition."""

    sample_id: str
    partition: PartitionName
    order: PendingOrder

    def __post_init__(self) -> None:
        if not self.sample_id.strip():
            raise ValueError("scheduled order sample ID must not be empty")
        if not isinstance(self.partition, PartitionName):
            raise ValueError("scheduled order partition is not supported")


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """Stable audit record for one simulation event and resulting account state."""

    sequence: int
    occurred_at: datetime
    symbol: str
    timeframe: str
    candle_open_time: datetime
    submitted_signal_ids: tuple[UUID, ...]
    opening_fill_ids: tuple[UUID, ...]
    fill_decisions: tuple[ReplayFillDecision, ...]
    protective_exits: tuple[ReplayProtectiveExit, ...]
    expired_signal_ids: tuple[UUID, ...]
    cash_balance: str
    equity: str
    margin_used: str
    free_margin: str
    pending_order_count: int
    open_position_count: int
    closed_trade_count: int

    @classmethod
    def from_step(
        cls,
        *,
        sequence: int,
        step: BacktestStep,
        submitted_signal_ids: tuple[UUID, ...],
        engine: BacktestEngine,
    ) -> "ReplayEvent":
        candle = step.event.candle
        return cls(
            sequence=sequence,
            occurred_at=step.event.occurred_at,
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            candle_open_time=candle.open_time,
            submitted_signal_ids=submitted_signal_ids,
            opening_fill_ids=tuple(fill.id for fill in step.opening_fills),
            fill_decisions=tuple(
                ReplayFillDecision(
                    status=decision.status.value,
                    requested_volume=str(decision.requested_volume),
                    filled_volume=str(decision.filled_volume),
                    remaining_volume=str(decision.remaining_volume),
                    margin_required=str(decision.margin_required),
                    reason_codes=tuple(reason.value for reason in decision.reason_codes),
                )
                for decision in step.fill_decisions
            ),
            protective_exits=tuple(
                ReplayProtectiveExit(
                    fill_id=exit_result.fill.id,
                    reason=exit_result.reason.value,
                    ambiguous=exit_result.ambiguous,
                )
                for exit_result in step.protective_exits
            ),
            expired_signal_ids=step.expired_signal_ids,
            cash_balance=str(step.portfolio.cash_balance),
            equity=str(step.portfolio.equity),
            margin_used=str(step.portfolio.margin_used),
            free_margin=str(step.portfolio.free_margin),
            pending_order_count=len(engine.pending_orders),
            open_position_count=len(step.portfolio.positions),
            closed_trade_count=len(step.portfolio.closed_trades),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "occurred_at": self.occurred_at.isoformat(),
            "candle": {
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "open_time": self.candle_open_time.isoformat(),
            },
            "submitted_signal_ids": [str(value) for value in self.submitted_signal_ids],
            "opening_fill_ids": [str(value) for value in self.opening_fill_ids],
            "fill_decisions": [item.to_record() for item in self.fill_decisions],
            "protective_exits": [item.to_record() for item in self.protective_exits],
            "expired_signal_ids": [str(value) for value in self.expired_signal_ids],
            "portfolio": {
                "cash_balance": self.cash_balance,
                "equity": self.equity,
                "margin_used": self.margin_used,
                "free_margin": self.free_margin,
                "pending_order_count": self.pending_order_count,
                "open_position_count": self.open_position_count,
                "closed_trade_count": self.closed_trade_count,
            },
        }


@dataclass(frozen=True, slots=True)
class CompletedExperiment:
    """Fully reconciled Phase 3 result, still restricted to research use."""

    manifest: ReproducibilityManifest
    report: BaselineReport
    artifacts: BaselineArtifacts
    journal: TradeJournal
    replay: tuple[ReplayEvent, ...]
    final_portfolio: PortfolioState

    def __post_init__(self) -> None:
        if self.report.manifest != self.manifest:
            raise ValueError("completed report and manifest do not match")
        if self.report.promotion_decision != "RESEARCH_ONLY":
            raise ValueError("completed experiment must remain research only")


def _partition_samples(
    split: ChronologicalDatasetSplit,
) -> dict[PartitionName, tuple[TemporalSample, ...]]:
    return {
        PartitionName.TRAINING: split.training,
        PartitionName.VALIDATION: split.validation,
        PartitionName.TEST: split.test,
    }


def _validate_inputs(
    *,
    config: ExperimentConfig,
    split: ChronologicalDatasetSplit,
    engine: BacktestEngine,
    scheduled_orders: tuple[ScheduledOrder, ...],
) -> None:
    if engine.clock.cursor != 0:
        raise ValueError("experiment runner requires a pristine simulation clock")
    if engine.pending_orders or engine.portfolio.positions or engine.portfolio.closed_trades:
        raise ValueError("experiment runner requires a pristine engine")
    if engine.portfolio.cash_balance != config.initial_equity:
        raise ValueError("engine initial cash does not match experiment configuration")
    if tuple(sorted(instrument.symbol for instrument in engine.instruments)) != config.symbols:
        raise ValueError("engine instruments do not match experiment symbols")
    if any(
        event.candle.timeframe != config.timeframe
        or event.candle.open_time < config.period_start
        or event.candle.close_time > config.period_end
        for event in engine.clock.events
    ):
        raise ValueError("historical candles fall outside the configured timeframe or period")
    if any(model.scenario != config.cost_scenario for _, model in engine.cost_models):
        raise ValueError("engine cost scenario does not match experiment configuration")

    signal_ids = tuple(item.order.signal.id for item in scheduled_orders)
    sample_ids = tuple(item.sample_id for item in scheduled_orders)
    if len(signal_ids) != len(set(signal_ids)):
        raise ValueError("scheduled signal IDs must be unique")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("scheduled sample IDs must be unique")
    canonical = tuple(
        sorted(
            scheduled_orders,
            key=lambda item: (
                item.order.signal.observed_at,
                item.order.signal.symbol,
                item.order.signal.timeframe,
                str(item.order.signal.id),
            ),
        )
    )
    if scheduled_orders != canonical:
        raise ValueError("scheduled orders must use canonical chronological order")

    samples_by_partition = _partition_samples(split)
    excluded_ids = {sample.id for sample in split.excluded}
    source_events = {
        (event.candle.symbol, event.candle.timeframe, event.occurred_at)
        for event in engine.clock.events
    }
    for item in scheduled_orders:
        signal = item.order.signal
        matches = tuple(
            sample for sample in samples_by_partition[item.partition] if sample.id == item.sample_id
        )
        if not matches:
            if item.sample_id in excluded_ids:
                raise ValueError("scheduled order references an excluded leakage-buffer sample")
            raise ValueError("scheduled order sample is absent from its declared partition")
        if matches[0].observed_at != signal.observed_at:
            raise ValueError("scheduled order observation does not match its sample")
        if (signal.symbol, signal.timeframe) not in {
            (symbol, config.timeframe) for symbol in config.symbols
        }:
            raise ValueError("scheduled signal falls outside the experiment universe")
        if (signal.symbol, signal.timeframe, signal.observed_at) not in source_events:
            raise ValueError("scheduled signal is not anchored to an observable source candle")


def run_experiment(
    *,
    config: ExperimentConfig,
    split: ChronologicalDatasetSplit,
    engine: BacktestEngine,
    scheduled_orders: tuple[ScheduledOrder, ...],
) -> CompletedExperiment:
    """Replay one complete experiment and emit reconciled, checksummed artifacts."""

    _validate_inputs(
        config=config,
        split=split,
        engine=engine,
        scheduled_orders=scheduled_orders,
    )
    partition_by_signal = {item.order.signal.id: item.partition for item in scheduled_orders}
    orders_by_source: dict[tuple[str, str, datetime], list[ScheduledOrder]] = {}
    for scheduled in scheduled_orders:
        signal = scheduled.order.signal
        orders_by_source.setdefault(
            (signal.symbol, signal.timeframe, signal.observed_at), []
        ).append(scheduled)
    submitted_count = 0
    replay: list[ReplayEvent] = []
    current = engine
    while not current.clock.is_finished:
        step, current = current.step()
        submitted: list[UUID] = []
        source_key = (
            step.event.candle.symbol,
            step.event.candle.timeframe,
            step.event.occurred_at,
        )
        for scheduled in orders_by_source.get(source_key, ()):
            current = current.submit(scheduled.order)
            submitted.append(scheduled.order.signal.id)
            submitted_count += 1
        replay.append(
            ReplayEvent.from_step(
                sequence=len(replay),
                step=step,
                submitted_signal_ids=tuple(submitted),
                engine=current,
            )
        )

    if submitted_count != len(scheduled_orders):
        raise ValueError("not every scheduled order was observed during replay")
    if current.pending_orders:
        raise ValueError("experiment ended with pending orders")
    if current.portfolio.positions:
        raise ValueError("experiment ended with open positions")

    journal = TradeJournal.from_portfolio(current.portfolio)
    journal.reconcile(initial_cash=config.initial_equity, final_portfolio=current.portfolio)
    unknown_signals = {
        trade.opening_signal_id for trade in journal.trades
    } - partition_by_signal.keys()
    if unknown_signals:
        raise ValueError("closed trade does not belong to a scheduled signal")
    journals = {
        name: TradeJournal(
            trades=tuple(
                trade
                for trade in journal.trades
                if partition_by_signal[trade.opening_signal_id] is name
            )
        )
        for name in PartitionName
    }
    manifest = build_reproducibility_manifest(config=config, split=split)
    report, report_journal = build_baseline_report(
        config=config,
        manifest=manifest,
        split=split,
        training=journals[PartitionName.TRAINING],
        validation=journals[PartitionName.VALIDATION],
        test=journals[PartitionName.TEST],
    )
    if report_journal != journal:
        raise ValueError("partitioned journals do not reconstruct the full trade journal")
    event_records = tuple(event.to_record() for event in replay)
    artifacts = build_baseline_artifacts(
        report=report,
        journal=journal,
        event_records=event_records,
    )
    return CompletedExperiment(
        manifest=manifest,
        report=report,
        artifacts=artifacts,
        journal=journal,
        replay=tuple(replay),
        final_portfolio=current.portfolio,
    )
