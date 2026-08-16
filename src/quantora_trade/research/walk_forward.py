"""Chronological walk-forward evaluation with purged label boundaries."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from quantora_trade.research.baseline import (
    BaselineTrainingConfig,
    ClassificationMetrics,
    LogisticBaselineModel,
    evaluate_classifier,
    train_logistic_baseline,
)
from quantora_trade.research.dataset import DirectionLabel, LabeledExample, ResearchDataset

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    version: str = "walk-forward-v1"
    training_size: int = 120
    validation_size: int = 40
    test_size: int = 40
    step_size: int = 40
    expanding_training: bool = True
    purge: timedelta = timedelta(0)
    embargo: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("walk-forward version must not be empty")
        if min(self.training_size, self.validation_size, self.test_size, self.step_size) <= 0:
            raise ValueError("walk-forward window sizes must be greater than zero")
        if self.purge < timedelta(0) or self.embargo < timedelta(0):
            raise ValueError("walk-forward purge and embargo must be non-negative")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "training_size": self.training_size,
            "validation_size": self.validation_size,
            "test_size": self.test_size,
            "step_size": self.step_size,
            "expanding_training": self.expanding_training,
            "purge_seconds": str(Decimal(str(self.purge.total_seconds()))),
            "embargo_seconds": str(Decimal(str(self.embargo.total_seconds()))),
        }


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    index: int
    training: tuple[LabeledExample, ...]
    validation: tuple[LabeledExample, ...]
    test: tuple[LabeledExample, ...]
    excluded_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("walk-forward fold index must be non-negative")
        if not self.training or not self.validation or not self.test:
            raise ValueError("walk-forward partitions must not be empty")
        groups = (self.training, self.validation, self.test)
        all_ids = tuple(item.id for group in groups for item in group)
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("walk-forward fold has overlapping partitions")
        if self.excluded_ids != tuple(sorted(set(self.excluded_ids), key=str)):
            raise ValueError("walk-forward excluded IDs must be canonical and unique")
        if set(self.excluded_ids) & set(all_ids):
            raise ValueError("walk-forward excluded IDs overlap an included partition")
        if max(item.features.observed_at for item in self.training) >= min(
            item.features.observed_at for item in self.validation
        ):
            raise ValueError("training must precede validation")
        if max(item.features.observed_at for item in self.validation) >= min(
            item.features.observed_at for item in self.test
        ):
            raise ValueError("validation must precede test")


@dataclass(frozen=True, slots=True)
class NoSkillMetrics:
    probability_up: Decimal
    accuracy: Decimal
    brier_score: Decimal


@dataclass(frozen=True, slots=True)
class FoldEvaluation:
    fold_index: int
    model: LogisticBaselineModel
    validation: ClassificationMetrics
    test: ClassificationMetrics
    no_skill_test: NoSkillMetrics


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    id: UUID
    dataset_sha256: str
    config: WalkForwardConfig
    training_config: BaselineTrainingConfig
    folds: tuple[FoldEvaluation, ...]
    weighted_test_accuracy: Decimal
    weighted_test_brier: Decimal
    weighted_no_skill_brier: Decimal
    promotion_decision: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.folds:
            raise ValueError("walk-forward result requires at least one fold")
        if not _SHA256.fullmatch(self.dataset_sha256):
            raise ValueError("walk-forward dataset checksum must be a lowercase SHA-256")
        if tuple(item.fold_index for item in self.folds) != tuple(range(len(self.folds))):
            raise ValueError("walk-forward evaluations must use canonical fold indexes")
        if self.promotion_decision != "RESEARCH_ONLY":
            raise ValueError("walk-forward evaluation cannot authorize trading")
        expected = _result_payload(
            dataset_sha256=self.dataset_sha256,
            config=self.config,
            training_config=self.training_config,
            folds=self.folds,
            weighted_test_accuracy=self.weighted_test_accuracy,
            weighted_test_brier=self.weighted_test_brier,
            weighted_no_skill_brier=self.weighted_no_skill_brier,
            promotion_decision=self.promotion_decision,
        )
        checksum = hashlib.sha256(_canonical_json(expected).encode()).hexdigest()
        if self.sha256 != checksum or self.id != uuid5(NAMESPACE_URL, checksum):
            raise ValueError("walk-forward result identity does not match its contents")

    def to_record(self) -> dict[str, Any]:
        record = _result_payload(
            dataset_sha256=self.dataset_sha256,
            config=self.config,
            training_config=self.training_config,
            folds=self.folds,
            weighted_test_accuracy=self.weighted_test_accuracy,
            weighted_test_brier=self.weighted_test_brier,
            weighted_no_skill_brier=self.weighted_no_skill_brier,
            promotion_decision=self.promotion_decision,
        )
        record.update({"id": str(self.id), "sha256": self.sha256})
        return record


def build_walk_forward_folds(
    *, dataset: ResearchDataset, config: WalkForwardConfig
) -> tuple[WalkForwardFold, ...]:
    """Create rolling folds and remove examples whose labels cross a future boundary."""

    examples = dataset.examples
    first_test_start = config.training_size + config.validation_size
    folds: list[WalkForwardFold] = []
    test_start = first_test_start
    while test_start + config.test_size <= len(examples):
        validation_start = test_start - config.validation_size
        training_start = 0 if config.expanding_training else validation_start - config.training_size
        raw_training = examples[training_start:validation_start]
        raw_validation = examples[validation_start:test_start]
        raw_test = examples[test_start : test_start + config.test_size]
        validation_boundary = raw_validation[0].features.observed_at
        test_boundary = raw_test[0].features.observed_at
        training = tuple(
            item for item in raw_training if item.label_end_at <= validation_boundary - config.purge
        )
        validation = tuple(
            item
            for item in raw_validation
            if item.features.observed_at >= validation_boundary + config.embargo
            and item.label_end_at <= test_boundary - config.purge
        )
        test = tuple(
            item for item in raw_test if item.features.observed_at >= test_boundary + config.embargo
        )
        included = {item.id for group in (training, validation, test) for item in group}
        excluded = tuple(
            sorted(
                (
                    item.id
                    for item in (*raw_training, *raw_validation, *raw_test)
                    if item.id not in included
                ),
                key=str,
            )
        )
        folds.append(
            WalkForwardFold(
                index=len(folds),
                training=training,
                validation=validation,
                test=test,
                excluded_ids=excluded,
            )
        )
        test_start += config.step_size
    if not folds:
        raise ValueError("dataset is too small for the walk-forward configuration")
    return tuple(folds)


def _no_skill_metrics(
    *, training: tuple[LabeledExample, ...], test: tuple[LabeledExample, ...]
) -> NoSkillMetrics:
    probability_up = Decimal(sum(item.label is DirectionLabel.UP for item in training)) / Decimal(
        len(training)
    )
    predicted = DirectionLabel.UP if probability_up >= Decimal("0.5") else DirectionLabel.DOWN
    accuracy = Decimal(sum(item.label is predicted for item in test)) / Decimal(len(test))
    brier = sum(
        ((probability_up - Decimal(item.label is DirectionLabel.UP)) ** 2 for item in test),
        Decimal("0"),
    ) / Decimal(len(test))
    return NoSkillMetrics(
        probability_up=probability_up,
        accuracy=accuracy,
        brier_score=brier,
    )


def _metrics_record(metrics: ClassificationMetrics) -> dict[str, Any]:
    return {
        "example_count": metrics.example_count,
        "accuracy": str(metrics.accuracy),
        "brier_score": str(metrics.brier_score),
        "log_loss": str(metrics.log_loss),
    }


def _result_payload(
    *,
    dataset_sha256: str,
    config: WalkForwardConfig,
    training_config: BaselineTrainingConfig,
    folds: tuple[FoldEvaluation, ...],
    weighted_test_accuracy: Decimal,
    weighted_test_brier: Decimal,
    weighted_no_skill_brier: Decimal,
    promotion_decision: str,
) -> dict[str, Any]:
    return {
        "dataset_sha256": dataset_sha256,
        "config": config.to_record(),
        "training_config": training_config.to_record(),
        "folds": [
            {
                "fold_index": item.fold_index,
                "model_id": str(item.model.id),
                "model_sha256": item.model.sha256,
                "validation": _metrics_record(item.validation),
                "test": _metrics_record(item.test),
                "no_skill_test": {
                    "probability_up": str(item.no_skill_test.probability_up),
                    "accuracy": str(item.no_skill_test.accuracy),
                    "brier_score": str(item.no_skill_test.brier_score),
                },
            }
            for item in folds
        ],
        "weighted_test_accuracy": str(weighted_test_accuracy),
        "weighted_test_brier": str(weighted_test_brier),
        "weighted_no_skill_brier": str(weighted_no_skill_brier),
        "promotion_decision": promotion_decision,
    }


def run_walk_forward(
    *,
    dataset: ResearchDataset,
    config: WalkForwardConfig,
    training_config: BaselineTrainingConfig | None = None,
) -> WalkForwardResult:
    """Train independently per fold and compare OOS calibration with a no-skill prior."""

    effective_training = training_config or BaselineTrainingConfig()
    folds = build_walk_forward_folds(dataset=dataset, config=config)
    evaluations: list[FoldEvaluation] = []
    for fold in folds:
        model = train_logistic_baseline(
            examples=fold.training,
            dataset_sha256=dataset.sha256,
            config=effective_training,
        )
        evaluations.append(
            FoldEvaluation(
                fold_index=fold.index,
                model=model,
                validation=evaluate_classifier(model=model, examples=fold.validation),
                test=evaluate_classifier(model=model, examples=fold.test),
                no_skill_test=_no_skill_metrics(training=fold.training, test=fold.test),
            )
        )
    completed = tuple(evaluations)
    total = Decimal(sum(item.test.example_count for item in completed))
    weighted_accuracy = (
        sum(
            (item.test.accuracy * item.test.example_count for item in completed),
            Decimal("0"),
        )
        / total
    )
    weighted_brier = (
        sum(
            (item.test.brier_score * item.test.example_count for item in completed),
            Decimal("0"),
        )
        / total
    )
    weighted_no_skill = (
        sum(
            (item.no_skill_test.brier_score * item.test.example_count for item in completed),
            Decimal("0"),
        )
        / total
    )
    checksum = hashlib.sha256(
        _canonical_json(
            _result_payload(
                dataset_sha256=dataset.sha256,
                config=config,
                training_config=effective_training,
                folds=completed,
                weighted_test_accuracy=weighted_accuracy,
                weighted_test_brier=weighted_brier,
                weighted_no_skill_brier=weighted_no_skill,
                promotion_decision="RESEARCH_ONLY",
            )
        ).encode()
    ).hexdigest()
    return WalkForwardResult(
        id=uuid5(NAMESPACE_URL, checksum),
        dataset_sha256=dataset.sha256,
        config=config,
        training_config=effective_training,
        folds=completed,
        weighted_test_accuracy=weighted_accuracy,
        weighted_test_brier=weighted_brier,
        weighted_no_skill_brier=weighted_no_skill,
        promotion_decision="RESEARCH_ONLY",
        sha256=checksum,
    )
