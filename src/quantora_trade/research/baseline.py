"""Deterministic logistic baseline for offline directional research."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from quantora_trade.research.dataset import DirectionLabel, LabeledExample
from quantora_trade.research.features import FEATURE_NAMES, FeatureVector

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sigmoid(value: Decimal) -> Decimal:
    bounded = max(Decimal("-50"), min(Decimal("50"), value))
    with localcontext() as context:
        context.prec = 34
        return Decimal("1") / (Decimal("1") + (-bounded).exp())


@dataclass(frozen=True, slots=True)
class BaselineTrainingConfig:
    version: str = "logistic-baseline-v1"
    iterations: int = 200
    learning_rate: Decimal = Decimal("0.05")
    l2_penalty: Decimal = Decimal("0.001")
    minimum_examples: int = 20

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("baseline version must not be empty")
        if self.iterations <= 0 or self.minimum_examples < 2:
            raise ValueError("training iterations and minimum examples are invalid")
        if not self.learning_rate.is_finite() or self.learning_rate <= 0:
            raise ValueError("learning rate must be finite and greater than zero")
        if not self.l2_penalty.is_finite() or self.l2_penalty < 0:
            raise ValueError("L2 penalty must be finite and non-negative")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "iterations": self.iterations,
            "learning_rate": str(self.learning_rate),
            "l2_penalty": str(self.l2_penalty),
            "minimum_examples": self.minimum_examples,
        }


@dataclass(frozen=True, slots=True)
class AdvisoryPrediction:
    """Offline opinion only; deliberately contains no order, size, or risk fields."""

    id: UUID
    model_id: UUID
    symbol: str
    timeframe: str
    observed_at: datetime
    predicted_label: DirectionLabel
    probability_up: Decimal
    confidence: Decimal
    feature_schema_sha256: str

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != UTC.utcoffset(
            self.observed_at
        ):
            raise ValueError("prediction observation must be timezone-aware UTC")
        if not Decimal("0") <= self.probability_up <= Decimal("1"):
            raise ValueError("prediction probability must be between zero and one")
        expected_confidence = max(self.probability_up, Decimal("1") - self.probability_up)
        if self.confidence != expected_confidence:
            raise ValueError("prediction confidence does not match class probability")
        expected_label = (
            DirectionLabel.UP if self.probability_up >= Decimal("0.5") else DirectionLabel.DOWN
        )
        if self.predicted_label is not expected_label:
            raise ValueError("prediction label does not match class probability")
        expected_id = _prediction_identity(
            model_id=self.model_id,
            features_identity=(
                self.symbol,
                self.timeframe,
                self.observed_at.isoformat(),
                self.feature_schema_sha256,
            ),
            probability_up=self.probability_up,
        )
        if self.id != expected_id:
            raise ValueError("prediction ID does not match its inputs")


@dataclass(frozen=True, slots=True)
class LogisticBaselineModel:
    id: UUID
    version: str
    dataset_sha256: str
    feature_schema_sha256: str
    training_example_ids: tuple[UUID, ...]
    feature_names: tuple[str, ...]
    centers: tuple[Decimal, ...]
    scales: tuple[Decimal, ...]
    weights: tuple[Decimal, ...]
    intercept: Decimal
    training_config: BaselineTrainingConfig
    sha256: str

    def __post_init__(self) -> None:
        size = len(self.feature_names)
        if self.feature_names != FEATURE_NAMES:
            raise ValueError("model feature names do not match the canonical schema")
        if not self.version.strip():
            raise ValueError("model version must not be empty")
        if not _SHA256.fullmatch(self.dataset_sha256) or not _SHA256.fullmatch(
            self.feature_schema_sha256
        ):
            raise ValueError("model dataset and feature checksums must be SHA-256")
        if not size or any(
            len(values) != size for values in (self.centers, self.scales, self.weights)
        ):
            raise ValueError("model parameter dimensions do not match")
        if any(scale <= 0 or not scale.is_finite() for scale in self.scales):
            raise ValueError("model feature scales must be finite and greater than zero")
        parameters = (*self.centers, *self.scales, *self.weights, self.intercept)
        if any(not value.is_finite() for value in parameters):
            raise ValueError("model parameters must be finite")
        if len(self.training_example_ids) != len(set(self.training_example_ids)):
            raise ValueError("model training examples must be unique")
        record = self.to_record(include_sha=False)
        expected_sha = hashlib.sha256(_canonical_json(record).encode()).hexdigest()
        if self.sha256 != expected_sha:
            raise ValueError("model checksum does not match its parameters")
        if self.id != uuid5(NAMESPACE_URL, self.sha256):
            raise ValueError("model ID does not match its checksum")

    def to_record(self, *, include_sha: bool = True) -> dict[str, Any]:
        record = {
            "version": self.version,
            "dataset_sha256": self.dataset_sha256,
            "feature_schema_sha256": self.feature_schema_sha256,
            "training_example_ids": [str(value) for value in self.training_example_ids],
            "feature_names": list(self.feature_names),
            "centers": [str(value) for value in self.centers],
            "scales": [str(value) for value in self.scales],
            "weights": [str(value) for value in self.weights],
            "intercept": str(self.intercept),
            "training_config": self.training_config.to_record(),
        }
        if include_sha:
            record["id"] = str(self.id)
            record["sha256"] = self.sha256
        return record

    def predict(self, features: FeatureVector) -> AdvisoryPrediction:
        if features.schema_sha256 != self.feature_schema_sha256:
            raise ValueError("inference feature schema does not match the model")
        values = tuple(value for _, value in features.values)
        standardized = tuple(
            (value - center) / scale
            for value, center, scale in zip(values, self.centers, self.scales, strict=True)
        )
        score = self.intercept + sum(
            (weight * value for weight, value in zip(self.weights, standardized, strict=True)),
            Decimal("0"),
        )
        probability_up = _sigmoid(score)
        predicted = DirectionLabel.UP if probability_up >= Decimal("0.5") else DirectionLabel.DOWN
        prediction_id = _prediction_identity(
            model_id=self.id,
            features_identity=(
                features.symbol,
                features.timeframe,
                features.observed_at.isoformat(),
                features.schema_sha256,
            ),
            probability_up=probability_up,
        )
        return AdvisoryPrediction(
            id=prediction_id,
            model_id=self.id,
            symbol=features.symbol,
            timeframe=features.timeframe,
            observed_at=features.observed_at,
            predicted_label=predicted,
            probability_up=probability_up,
            confidence=max(probability_up, Decimal("1") - probability_up),
            feature_schema_sha256=features.schema_sha256,
        )


def _prediction_identity(
    *,
    model_id: UUID,
    features_identity: tuple[str, str, str, str],
    probability_up: Decimal,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        _canonical_json(
            {
                "model_id": str(model_id),
                "features_identity": list(features_identity),
                "probability_up": str(probability_up),
            }
        ),
    )


def _model_record(
    *,
    version: str,
    dataset_sha256: str,
    feature_schema_sha256: str,
    training_example_ids: tuple[UUID, ...],
    centers: tuple[Decimal, ...],
    scales: tuple[Decimal, ...],
    weights: tuple[Decimal, ...],
    intercept: Decimal,
    config: BaselineTrainingConfig,
) -> dict[str, Any]:
    return {
        "version": version,
        "dataset_sha256": dataset_sha256,
        "feature_schema_sha256": feature_schema_sha256,
        "training_example_ids": [str(value) for value in training_example_ids],
        "feature_names": list(FEATURE_NAMES),
        "centers": [str(value) for value in centers],
        "scales": [str(value) for value in scales],
        "weights": [str(value) for value in weights],
        "intercept": str(intercept),
        "training_config": config.to_record(),
    }


def train_logistic_baseline(
    *,
    examples: tuple[LabeledExample, ...],
    dataset_sha256: str,
    config: BaselineTrainingConfig | None = None,
) -> LogisticBaselineModel:
    """Fit standardization and weights from the supplied training examples only."""

    effective = config or BaselineTrainingConfig()
    if not _SHA256.fullmatch(dataset_sha256):
        raise ValueError("training dataset checksum must be a lowercase SHA-256")
    if len(examples) < effective.minimum_examples:
        raise ValueError("training partition does not contain enough examples")
    ordered = tuple(
        sorted(
            examples,
            key=lambda item: (
                item.features.observed_at,
                item.features.symbol,
                item.features.timeframe,
                str(item.id),
            ),
        )
    )
    if examples != ordered:
        raise ValueError("training examples must use canonical chronological order")
    schemas = {item.features.schema_sha256 for item in examples}
    if len(schemas) != 1:
        raise ValueError("training examples must share one feature schema")
    if {item.label for item in examples} != {DirectionLabel.DOWN, DirectionLabel.UP}:
        raise ValueError("training partition must contain both direction labels")

    rows = tuple(tuple(value for _, value in item.features.values) for item in examples)
    count = Decimal(len(rows))
    centers = tuple(
        sum((row[index] for row in rows), Decimal("0")) / count
        for index in range(len(FEATURE_NAMES))
    )
    scales_list: list[Decimal] = []
    for index, center in enumerate(centers):
        variance = sum(((row[index] - center) ** 2 for row in rows), Decimal("0")) / count
        scale = variance.sqrt() if variance > 0 else Decimal("1")
        scales_list.append(scale)
    scales = tuple(scales_list)
    standardized = tuple(
        tuple(
            (value - center) / scale
            for value, center, scale in zip(row, centers, scales, strict=True)
        )
        for row in rows
    )
    targets = tuple(Decimal(item.label is DirectionLabel.UP) for item in examples)
    weights = [Decimal("0")] * len(FEATURE_NAMES)
    intercept = Decimal("0")
    for _ in range(effective.iterations):
        errors = tuple(
            _sigmoid(
                intercept
                + sum(
                    (weight * value for weight, value in zip(weights, row, strict=True)),
                    Decimal("0"),
                )
            )
            - target
            for row, target in zip(standardized, targets, strict=True)
        )
        intercept -= effective.learning_rate * (sum(errors, Decimal("0")) / count)
        for index in range(len(weights)):
            gradient = (
                sum(
                    (error * row[index] for error, row in zip(errors, standardized, strict=True)),
                    Decimal("0"),
                )
                / count
            ) + (effective.l2_penalty * weights[index])
            weights[index] -= effective.learning_rate * gradient

    training_ids = tuple(item.id for item in examples)
    record = _model_record(
        version=effective.version,
        dataset_sha256=dataset_sha256,
        feature_schema_sha256=examples[0].features.schema_sha256,
        training_example_ids=training_ids,
        centers=centers,
        scales=scales,
        weights=tuple(weights),
        intercept=intercept,
        config=effective,
    )
    final_sha = hashlib.sha256(_canonical_json(record).encode()).hexdigest()
    return LogisticBaselineModel(
        id=uuid5(NAMESPACE_URL, final_sha),
        version=effective.version,
        dataset_sha256=dataset_sha256,
        feature_schema_sha256=examples[0].features.schema_sha256,
        training_example_ids=training_ids,
        feature_names=FEATURE_NAMES,
        centers=centers,
        scales=scales,
        weights=tuple(weights),
        intercept=intercept,
        training_config=effective,
        sha256=final_sha,
    )


@dataclass(frozen=True, slots=True)
class ClassificationMetrics:
    example_count: int
    accuracy: Decimal
    brier_score: Decimal
    log_loss: Decimal


def evaluate_classifier(
    *, model: LogisticBaselineModel, examples: tuple[LabeledExample, ...]
) -> ClassificationMetrics:
    if not examples:
        raise ValueError("classification evaluation requires examples")
    predictions = tuple(model.predict(item.features) for item in examples)
    targets = tuple(Decimal(item.label is DirectionLabel.UP) for item in examples)
    count = Decimal(len(examples))
    accuracy = (
        Decimal(
            sum(
                prediction.predicted_label is example.label
                for prediction, example in zip(predictions, examples, strict=True)
            )
        )
        / count
    )
    brier = (
        sum(
            (
                (prediction.probability_up - target) ** 2
                for prediction, target in zip(predictions, targets, strict=True)
            ),
            Decimal("0"),
        )
        / count
    )
    epsilon = Decimal("1e-24")
    losses: list[Decimal] = []
    for prediction, target in zip(predictions, targets, strict=True):
        probability = max(epsilon, min(Decimal("1") - epsilon, prediction.probability_up))
        losses.append(
            -(
                (target * probability.ln())
                + ((Decimal("1") - target) * (Decimal("1") - probability).ln())
            )
        )
    return ClassificationMetrics(
        example_count=len(examples),
        accuracy=accuracy,
        brier_score=brier,
        log_loss=sum(losses, Decimal("0")) / count,
    )
