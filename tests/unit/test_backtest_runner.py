"""Golden multi-symbol tests for the complete Phase 3 experiment workflow."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantora_trade.backtesting.engine import BacktestEngine, PendingOrder
from quantora_trade.backtesting.execution import ExecutionCostModel
from quantora_trade.backtesting.experiment import ExperimentConfig
from quantora_trade.backtesting.reporting import PartitionName, persist_baseline_artifacts
from quantora_trade.backtesting.runner import ScheduledOrder, run_experiment
from quantora_trade.backtesting.splits import TemporalSample, chronological_split
from quantora_trade.domain.enums import Action, AssetClass, SignalReasonCode
from quantora_trade.domain.models import Candle, Instrument
from quantora_trade.strategy.signals import build_signal

START = datetime(2025, 1, 1, tzinfo=UTC)


def samples() -> tuple[TemporalSample, ...]:
    return tuple(
        TemporalSample(
            id=f"sample-{index}",
            observed_at=START + timedelta(days=index, minutes=15),
            label_end_at=START + timedelta(days=index, minutes=30),
        )
        for index in range(10)
    )


def dataset_split():
    return chronological_split(
        samples=samples(),
        training_fraction=Decimal("0.5"),
        validation_fraction=Decimal("0.3"),
    )


def config() -> ExperimentConfig:
    return ExperimentConfig(
        run_name="phase-3-golden-baseline",
        code_commit_sha="b" * 40,
        dirty_worktree=False,
        official=True,
        engine_version="0.1.0",
        broker_profile_version="golden-broker-v1",
        strategy_version="technical-v1",
        risk_policy_version="risk-backtest-v1",
        dataset_id="golden-multi-symbol-v1",
        dataset_sha256="a" * 64,
        symbols=("EURUSD", "XAUUSD"),
        timeframe="M15",
        period_start=START,
        period_end=START + timedelta(days=10, hours=1),
        initial_equity=Decimal("1000"),
        account_currency="USD",
        cost_scenario="base",
        random_seed=42,
    )


def instrument(symbol: str) -> Instrument:
    is_gold = symbol == "XAUUSD"
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.METAL if is_gold else AssetClass.FOREX,
        quote_currency="USD",
        digits=2 if is_gold else 4,
        point=Decimal("0.01") if is_gold else Decimal("0.0001"),
        pip_size=Decimal("0.01") if is_gold else Decimal("0.0001"),
        tick_size=Decimal("0.01") if is_gold else Decimal("0.0001"),
        tick_value=Decimal("1") if is_gold else Decimal("10"),
        contract_size=Decimal("100") if is_gold else Decimal("100000"),
        spread_points=0,
        session_timezone="UTC",
        session_profile="24x5",
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
    )


def candle_pair(*, sample_index: int, symbol: str) -> tuple[Candle, Candle]:
    observed_at = samples()[sample_index].observed_at
    is_gold = symbol == "XAUUSD"
    source_open = Decimal("99.5") if is_gold else Decimal("1.0995")
    entry_open = Decimal("100") if is_gold else Decimal("1.1000")
    target = Decimal("101") if is_gold else Decimal("1.1010")
    source = Candle(
        symbol=symbol,
        timeframe="M15",
        open_time=observed_at - timedelta(minutes=15),
        close_time=observed_at,
        open=source_open,
        high=entry_open,
        low=source_open,
        close=source_open,
        tick_volume=100,
        is_closed=True,
    )
    entry = Candle(
        symbol=symbol,
        timeframe="M15",
        open_time=observed_at,
        close_time=observed_at + timedelta(minutes=15),
        open=entry_open,
        high=target,
        low=entry_open,
        close=target,
        tick_volume=120,
        is_closed=True,
    )
    return source, entry


def fixture():
    specifications = (instrument("EURUSD"), instrument("XAUUSD"))
    pairs = (
        (1, "XAUUSD", PartitionName.TRAINING),
        (6, "EURUSD", PartitionName.VALIDATION),
        (8, "XAUUSD", PartitionName.TEST),
    )
    candles: list[Candle] = []
    orders: list[ScheduledOrder] = []
    for sample_index, symbol, partition in pairs:
        source, entry = candle_pair(sample_index=sample_index, symbol=symbol)
        candles.extend((source, entry))
        signal = build_signal(
            candle=source,
            action=Action.BUY,
            confidence=Decimal("0.75"),
            strategy_version="technical-v1",
            reason_codes=(SignalReasonCode.EMA_BULLISH_ALIGNMENT,),
        )
        orders.append(
            ScheduledOrder(
                sample_id=f"sample-{sample_index}",
                partition=partition,
                order=PendingOrder(
                    signal=signal,
                    volume=Decimal("1") if symbol == "XAUUSD" else Decimal("0.1"),
                    stop_loss=Decimal("99") if symbol == "XAUUSD" else Decimal("1.0990"),
                    take_profit=Decimal("101") if symbol == "XAUUSD" else Decimal("1.1010"),
                ),
            )
        )
    costs = tuple(
        (
            item.symbol,
            ExecutionCostModel(
                point=item.point,
                spread_points=Decimal("0"),
                slippage_points=Decimal("0"),
                commission_per_side=Decimal("1"),
                scenario="base",
            ),
        )
        for item in specifications
    )
    engine = BacktestEngine.create(
        candles=tuple(candles),
        instruments=specifications,
        cost_models=costs,
        initial_cash=Decimal("1000"),
    )
    return engine, tuple(orders)


def run_fixture():
    engine, orders = fixture()
    return run_experiment(
        config=config(), split=dataset_split(), engine=engine, scheduled_orders=orders
    )


def test_golden_multi_symbol_experiment_is_reproducible_and_reconciled(tmp_path: Path) -> None:
    result = run_fixture()
    repeated = run_fixture()

    assert result == repeated
    assert result.report.overall.net_pnl == Decimal("205.80")
    assert result.report.overall.trade_count == 3
    assert tuple(item.metrics.net_pnl for item in result.report.partitions) == (
        Decimal("98"),
        Decimal("9.80"),
        Decimal("98"),
    )
    assert result.final_portfolio.cash_balance == Decimal("1205.80")
    assert result.final_portfolio.positions == ()
    assert len(result.replay) == 6
    assert sum(len(event.submitted_signal_ids) for event in result.replay) == 3
    assert result.report.promotion_decision == "RESEARCH_ONLY"
    assert (
        result.report.sha256 == "c59a5113cfbb15b93c26d2059aa9890cc6753271923b6ff30b75450c3cea5a98"
    )
    assert tuple(file.name for file in result.artifacts.files) == (
        "checksums.json",
        "events.json",
        "manifest.json",
        "report.html",
        "summary.json",
        "trades.json",
    )
    assert b"205.80" in result.artifacts.get("report.html").content
    assert len(json.loads(result.artifacts.get("events.json").content)) == 6

    persisted = persist_baseline_artifacts(
        artifacts=result.artifacts, output_directory=tmp_path / "phase-3-run"
    )
    assert persisted.directory == (tmp_path / "phase-3-run").resolve()
    checksums = json.loads((persisted.directory / "checksums.json").read_bytes())
    for name, expected in checksums.items():
        assert hashlib.sha256((persisted.directory / name).read_bytes()).hexdigest() == expected
    with pytest.raises(FileExistsError, match="already exists"):
        persist_baseline_artifacts(artifacts=result.artifacts, output_directory=persisted.directory)


def test_runner_rejects_wrong_partition_costs_and_nonflat_completion() -> None:
    engine, orders = fixture()
    with pytest.raises(ValueError, match="declared partition"):
        run_experiment(
            config=config(),
            split=dataset_split(),
            engine=engine,
            scheduled_orders=(replace(orders[0], partition=PartitionName.TEST), *orders[1:]),
        )

    wrong_cost_engine = replace(
        engine,
        cost_models=tuple(
            (symbol, replace(model, scenario="stress")) for symbol, model in engine.cost_models
        ),
    )
    with pytest.raises(ValueError, match="cost scenario"):
        run_experiment(
            config=config(),
            split=dataset_split(),
            engine=wrong_cost_engine,
            scheduled_orders=orders,
        )

    first = orders[0]
    no_target = replace(first, order=replace(first.order, take_profit=None))
    with pytest.raises(ValueError, match="open positions"):
        run_experiment(
            config=config(),
            split=dataset_split(),
            engine=engine,
            scheduled_orders=(no_target, *orders[1:]),
        )
