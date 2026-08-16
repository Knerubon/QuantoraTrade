"""End-to-end tests for leakage-safe Phase 4 research primitives."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.domain.models import Candle
from quantora_trade.research.baseline import (
    BaselineTrainingConfig,
    evaluate_classifier,
    train_logistic_baseline,
)
from quantora_trade.research.dataset import DatasetBuildConfig, build_research_dataset
from quantora_trade.research.features import build_feature_set
from quantora_trade.research.registry import (
    ModelCard,
    ModelLifecycle,
    ModelRegistry,
    RegistryInferenceService,
)
from quantora_trade.research.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_folds,
    run_walk_forward,
)

START = datetime(2025, 1, 1, tzinfo=UTC)


def candle_series(symbol: str, *, count: int = 180) -> tuple[Candle, ...]:
    scale = Decimal("1") if symbol == "XAUUSD" else Decimal("0.001")
    base = Decimal("2000") if symbol == "XAUUSD" else Decimal("1.1000")
    closes: list[Decimal] = []
    for index in range(count):
        cycle = Decimal((index % 10) - 5) * Decimal("0.10") * scale
        closes.append(base + (Decimal(index) * Decimal("0.02") * scale) + cycle)
    candles: list[Candle] = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index else close - (Decimal("0.03") * scale)
        open_time = START + timedelta(minutes=15 * index)
        padding = Decimal("0.05") * scale
        candles.append(
            Candle(
                symbol=symbol,
                timeframe="M15",
                open_time=open_time,
                close_time=open_time + timedelta(minutes=15),
                open=open_price,
                high=max(open_price, close) + padding,
                low=min(open_price, close) - padding,
                close=close,
                tick_volume=100 + index,
                is_closed=True,
            )
        )
    return tuple(candles)


def dataset():
    return build_research_dataset(
        candle_series=(candle_series("XAUUSD"), candle_series("EURUSD")),
        config=DatasetBuildConfig(
            version="direction-1bar-v1",
            source_dataset_id="phase-4-golden-v1",
            source_dataset_sha256="a" * 64,
            horizon_bars=1,
        ),
    )


def training_config() -> BaselineTrainingConfig:
    return BaselineTrainingConfig(
        version="logistic-golden-v1",
        iterations=40,
        learning_rate=Decimal("0.05"),
        l2_penalty=Decimal("0.001"),
        minimum_examples=20,
    )


def test_feature_pipeline_is_point_in_time_and_prefix_invariant() -> None:
    candles = candle_series("XAUUSD", count=90)
    prefix = build_feature_set(candles=candles[:75])
    complete = build_feature_set(candles=candles)

    assert prefix.vectors
    assert prefix.vectors == complete.vectors[: len(prefix.vectors)]
    assert all(vector.observed_at <= candles[74].close_time for vector in prefix.vectors)
    assert prefix.config.sha256 == complete.config.sha256
    assert prefix.sha256 != complete.sha256
    with pytest.raises(ValueError, match="canonical schema"):
        replace(prefix.vectors[0], values=prefix.vectors[0].values[:-1])


def test_versioned_dataset_is_deterministic_and_labels_explicit_future_windows() -> None:
    first = dataset()
    second = dataset()

    assert first == second
    assert len(first.feature_set_hashes) == 2
    assert len(first.examples) > 200
    assert {item.features.symbol for item in first.examples} == {"EURUSD", "XAUUSD"}
    assert all(item.label_end_at > item.features.observed_at for item in first.examples)
    assert all(
        item.to_temporal_sample().label_end_at == item.label_end_at for item in first.examples
    )
    changed = build_research_dataset(
        candle_series=(candle_series("XAUUSD"), candle_series("EURUSD")),
        config=replace(first.config, neutral_return_threshold=Decimal("0.000001")),
    )
    assert changed.sha256 != first.sha256


def test_logistic_baseline_fits_training_only_and_emits_advisory_predictions() -> None:
    research = dataset()
    training = research.examples[:80]
    test = research.examples[80:100]
    model = train_logistic_baseline(
        examples=training,
        dataset_sha256=research.sha256,
        config=training_config(),
    )
    repeated = train_logistic_baseline(
        examples=training,
        dataset_sha256=research.sha256,
        config=training_config(),
    )
    metrics = evaluate_classifier(model=model, examples=test)
    prediction = model.predict(test[0].features)

    assert model == repeated
    assert model.training_example_ids == tuple(item.id for item in training)
    assert prediction.model_id == model.id
    assert Decimal("0") <= prediction.probability_up <= Decimal("1")
    assert metrics.example_count == 20
    assert Decimal("0") <= metrics.accuracy <= Decimal("1")
    assert Decimal("0") <= metrics.brier_score <= Decimal("1")
    with pytest.raises(ValueError, match="schema"):
        model.predict(replace(test[0].features, schema_sha256="b" * 64))


def test_walk_forward_purges_boundaries_and_is_deterministic() -> None:
    research = dataset()
    config = WalkForwardConfig(
        version="phase-4-golden-walk-forward-v1",
        training_size=60,
        validation_size=20,
        test_size=20,
        step_size=20,
        purge=timedelta(minutes=15),
        embargo=timedelta(0),
    )
    folds = build_walk_forward_folds(dataset=research, config=config)
    result = run_walk_forward(
        dataset=research,
        config=config,
        training_config=training_config(),
    )
    repeated = run_walk_forward(
        dataset=research,
        config=config,
        training_config=training_config(),
    )

    assert result == repeated
    assert len(folds) == len(result.folds) >= 1
    for fold in folds:
        validation_start = min(item.features.observed_at for item in fold.validation)
        test_start = min(item.features.observed_at for item in fold.test)
        assert max(item.label_end_at for item in fold.training) <= validation_start
        assert max(item.label_end_at for item in fold.validation) <= test_start
    assert result.promotion_decision == "RESEARCH_ONLY"
    assert result.sha256 == "131f32a91c09dc8ce0c8ec2f16ceb3541df2dc17fcd335097884bf26c3f9133e"
    assert Decimal("0") <= result.weighted_test_brier <= Decimal("1")
    assert Decimal("0") <= result.weighted_no_skill_brier <= Decimal("1")
    assert result.weighted_test_brier > result.weighted_no_skill_brier


def test_registry_cannot_promote_model_beyond_research_challenger() -> None:
    research = dataset()
    model = train_logistic_baseline(
        examples=research.examples[:80],
        dataset_sha256=research.sha256,
        config=training_config(),
    )
    card = ModelCard(
        model=model,
        lifecycle=ModelLifecycle.RESEARCH_ONLY,
        evaluation_sha256="c" * 64,
        registered_at=START,
        limitations=("fixture data only", "no transaction-cost profitability evidence"),
    )
    registry = ModelRegistry().register(card)
    challenger = registry.change_lifecycle(model_id=model.id, lifecycle=ModelLifecycle.CHALLENGER)
    prediction = RegistryInferenceService(challenger).predict(
        model_id=model.id, features=research.examples[80].features
    )

    assert registry.cards[0].lifecycle is ModelLifecycle.RESEARCH_ONLY
    assert challenger.cards[0].lifecycle is ModelLifecycle.CHALLENGER
    assert prediction.model_id == model.id
    retired = challenger.change_lifecycle(model_id=model.id, lifecycle=ModelLifecycle.RETIRED)
    with pytest.raises(ValueError, match="retired"):
        RegistryInferenceService(retired).predict(
            model_id=model.id, features=research.examples[80].features
        )
    with pytest.raises(ValueError, match="permits promotion"):
        registry.change_lifecycle(model_id=model.id, lifecycle=ModelLifecycle.RESEARCH_ONLY)
