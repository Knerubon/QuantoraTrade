"""Tests for reproducible experiment manifests and baseline report artifacts."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from quantora_trade.backtesting.experiment import (
    ExperimentConfig,
    build_reproducibility_manifest,
)
from quantora_trade.backtesting.journal import TradeJournal
from quantora_trade.backtesting.portfolio import ClosedTrade
from quantora_trade.backtesting.reporting import (
    PartitionName,
    build_baseline_artifacts,
    build_baseline_report,
)
from quantora_trade.backtesting.splits import TemporalSample, chronological_split
from quantora_trade.domain.enums import Action

START = datetime(2025, 1, 1, tzinfo=UTC)


def config(*, dirty: bool = False, official: bool = True) -> ExperimentConfig:
    return ExperimentConfig(
        run_name="technical-baseline-v1",
        code_commit_sha="b" * 40,
        dirty_worktree=dirty,
        official=official,
        engine_version="0.1.0",
        strategy_version="technical-v1",
        risk_policy_version="risk-backtest-v1",
        dataset_id="fixture-multi-asset-v1",
        dataset_sha256="a" * 64,
        symbols=("EURUSD", "XAUUSD"),
        timeframe="M15",
        period_start=START,
        period_end=START + timedelta(days=10),
        initial_equity=Decimal("1000"),
        account_currency="USD",
        cost_scenario="base",
        random_seed=42,
    )


def split(
    *, training_fraction: Decimal = Decimal("0.5"), validation_fraction: Decimal = Decimal("0.3")
):
    samples = tuple(
        TemporalSample(
            id=f"sample-{index}",
            observed_at=START + timedelta(days=index),
            label_end_at=START + timedelta(days=index, hours=12),
        )
        for index in range(10)
    )
    return chronological_split(
        samples=samples,
        training_fraction=training_fraction,
        validation_fraction=validation_fraction,
    )


def trade(index: int, *, symbol: str, net_pnl: str) -> ClosedTrade:
    net = Decimal(net_pnl)
    return ClosedTrade(
        position_id=uuid4(),
        opening_fill_id=uuid4(),
        closing_fill_id=uuid4(),
        opening_signal_id=uuid4(),
        symbol=symbol,
        timeframe="M15",
        side=Action.BUY,
        volume=Decimal("1"),
        entry_reference_price=Decimal("100"),
        exit_reference_price=Decimal("101"),
        entry_price=Decimal("100"),
        exit_price=Decimal("101"),
        opened_at=START + timedelta(days=index),
        closed_at=START + timedelta(days=index, minutes=15),
        gross_pnl=net + Decimal("2"),
        execution_cost=Decimal("0"),
        entry_commission=Decimal("1"),
        exit_commission=Decimal("1"),
        net_pnl=net,
    )


def test_manifest_identity_is_deterministic_and_covers_split_membership() -> None:
    experiment = config()
    dataset_split = split()

    first = build_reproducibility_manifest(config=experiment, split=dataset_split)
    second = build_reproducibility_manifest(config=experiment, split=dataset_split)

    assert first == second
    assert first.config_sha256 == experiment.sha256
    assert first.split_counts == (
        ("excluded", 0),
        ("test", 2),
        ("training", 5),
        ("validation", 3),
    )
    assert first.to_record()["config"]["initial_equity"] == "1000"
    changed_split = split(training_fraction=Decimal("0.4"), validation_fraction=Decimal("0.3"))
    changed = build_reproducibility_manifest(config=experiment, split=changed_split)
    assert changed.run_id != first.run_id
    assert changed.split_sha256 != first.split_sha256


def test_official_manifest_rejects_dirty_worktree_and_invalid_identity() -> None:
    with pytest.raises(ValueError, match="clean worktree"):
        config(dirty=True)
    with pytest.raises(ValueError, match="SHA-1"):
        replace(config(), code_commit_sha="invalid")


def test_baseline_report_segments_results_and_builds_verified_artifacts() -> None:
    experiment = config()
    dataset_split = split()
    manifest = build_reproducibility_manifest(config=experiment, split=dataset_split)
    training = TradeJournal(trades=(trade(1, symbol="XAUUSD", net_pnl="10"),))
    validation = TradeJournal(trades=(trade(6, symbol="EURUSD", net_pnl="-4"),))
    test = TradeJournal(trades=(trade(9, symbol="XAUUSD", net_pnl="2"),))

    report, journal = build_baseline_report(
        config=experiment,
        manifest=manifest,
        split=dataset_split,
        training=training,
        validation=validation,
        test=test,
    )
    artifacts = build_baseline_artifacts(report=report, journal=journal)
    repeated = build_baseline_artifacts(report=report, journal=journal)

    assert report.overall.net_pnl == Decimal("8")
    assert report.overall.trade_count == 3
    assert tuple(item.name for item in report.partitions) == tuple(PartitionName)
    assert tuple(item.metrics.net_pnl for item in report.partitions) == (
        Decimal("10"),
        Decimal("-4"),
        Decimal("2"),
    )
    assert tuple(item.metrics.net_pnl for item in report.symbols) == (
        Decimal("-4"),
        Decimal("12"),
    )
    assert report.promotion_decision == "RESEARCH_ONLY"
    assert tuple(file.name for file in artifacts.files) == (
        "checksums.json",
        "manifest.json",
        "summary.json",
        "trades.json",
    )
    assert artifacts == repeated
    checksums = json.loads(artifacts.get("checksums.json").content)
    for name, expected in checksums.items():
        assert hashlib.sha256(artifacts.get(name).content).hexdigest() == expected
    assert json.loads(artifacts.get("summary.json").content)["overall"]["net_pnl"] == "8"
    with pytest.raises(ValueError, match="does not match"):
        build_baseline_artifacts(report=report, journal=TradeJournal())


def test_report_fails_when_trade_crosses_partition_or_uses_unknown_symbol() -> None:
    experiment = config()
    dataset_split = split()
    manifest = build_reproducibility_manifest(config=experiment, split=dataset_split)

    wrong_manifest = build_reproducibility_manifest(
        config=experiment,
        split=split(training_fraction=Decimal("0.4"), validation_fraction=Decimal("0.3")),
    )
    with pytest.raises(ValueError, match="dataset split"):
        build_baseline_report(
            config=experiment,
            manifest=wrong_manifest,
            split=dataset_split,
            training=TradeJournal(),
            validation=TradeJournal(),
            test=TradeJournal(),
        )

    with pytest.raises(ValueError, match="training period"):
        build_baseline_report(
            config=experiment,
            manifest=manifest,
            split=dataset_split,
            training=TradeJournal(trades=(trade(6, symbol="XAUUSD", net_pnl="1"),)),
            validation=TradeJournal(),
            test=TradeJournal(),
        )

    with pytest.raises(ValueError, match="outside experiment"):
        build_baseline_report(
            config=experiment,
            manifest=manifest,
            split=dataset_split,
            training=TradeJournal(trades=(trade(1, symbol="GBPUSD", net_pnl="1"),)),
            validation=TradeJournal(),
            test=TradeJournal(),
        )
