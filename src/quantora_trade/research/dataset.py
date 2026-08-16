"""Immutable, checksummed research datasets with explicit future-label windows."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from quantora_trade.backtesting.splits import TemporalSample
from quantora_trade.domain.models import Candle
from quantora_trade.research.features import (
    FeaturePipelineConfig,
    FeatureSet,
    FeatureVector,
    build_feature_set,
)
from quantora_trade.strategy.validation import validate_closed_candle_series

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class DirectionLabel(StrEnum):
    DOWN = "DOWN"
    UP = "UP"


@dataclass(frozen=True, slots=True)
class DatasetBuildConfig:
    """Pre-registered labeling and source-data identity."""

    version: str
    source_dataset_id: str
    source_dataset_sha256: str
    horizon_bars: int
    neutral_return_threshold: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.version.strip() or not self.source_dataset_id.strip():
            raise ValueError("dataset version and source ID must not be empty")
        if not _SHA256.fullmatch(self.source_dataset_sha256):
            raise ValueError("source dataset checksum must be a lowercase SHA-256")
        if self.horizon_bars <= 0:
            raise ValueError("label horizon must be greater than zero")
        if not self.neutral_return_threshold.is_finite() or self.neutral_return_threshold < 0:
            raise ValueError("neutral return threshold must be finite and non-negative")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_dataset_id": self.source_dataset_id,
            "source_dataset_sha256": self.source_dataset_sha256,
            "horizon_bars": self.horizon_bars,
            "neutral_return_threshold": str(self.neutral_return_threshold),
        }


@dataclass(frozen=True, slots=True)
class LabeledExample:
    id: UUID
    features: FeatureVector
    label: DirectionLabel
    label_return: Decimal
    label_end_at: datetime

    def __post_init__(self) -> None:
        if self.label_end_at.tzinfo is None or self.label_end_at.utcoffset() != UTC.utcoffset(
            self.label_end_at
        ):
            raise ValueError("label_end_at must be timezone-aware UTC")
        if self.label_end_at <= self.features.observed_at:
            raise ValueError("label window must end after feature observation")
        if not self.label_return.is_finite() or self.label_return == 0:
            raise ValueError("directional label return must be finite and non-zero")
        expected = DirectionLabel.UP if self.label_return > 0 else DirectionLabel.DOWN
        if self.label is not expected:
            raise ValueError("direction label does not match future return")
        identity = _example_identity(
            features=self.features,
            label=self.label,
            label_return=self.label_return,
            label_end_at=self.label_end_at,
        )
        if self.id != identity:
            raise ValueError("example ID does not match its point-in-time contents")

    def to_temporal_sample(self) -> TemporalSample:
        return TemporalSample(
            id=str(self.id),
            observed_at=self.features.observed_at,
            label_end_at=self.label_end_at,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "features": self.features.to_record(),
            "label": self.label.value,
            "label_return": str(self.label_return),
            "label_end_at": self.label_end_at.isoformat(),
        }


def _example_identity(
    *,
    features: FeatureVector,
    label: DirectionLabel,
    label_return: Decimal,
    label_end_at: datetime,
) -> UUID:
    record = {
        "features": features.to_record(),
        "label": label.value,
        "label_return": str(label_return),
        "label_end_at": label_end_at.isoformat(),
    }
    return uuid5(NAMESPACE_URL, _canonical_json(record))


@dataclass(frozen=True, slots=True)
class ResearchDataset:
    config: DatasetBuildConfig
    feature_config: FeaturePipelineConfig
    feature_set_hashes: tuple[str, ...]
    examples: tuple[LabeledExample, ...]
    excluded_neutral_count: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.examples:
            raise ValueError("research dataset requires at least one directional example")
        if self.excluded_neutral_count < 0:
            raise ValueError("excluded neutral count must be non-negative")
        if self.feature_set_hashes != tuple(sorted(set(self.feature_set_hashes))):
            raise ValueError("feature set hashes must be canonical and unique")
        if any(not _SHA256.fullmatch(value) for value in self.feature_set_hashes):
            raise ValueError("feature set checksums must be lowercase SHA-256")
        ordered = tuple(
            sorted(
                self.examples,
                key=lambda item: (
                    item.features.observed_at,
                    item.features.symbol,
                    item.features.timeframe,
                    str(item.id),
                ),
            )
        )
        if self.examples != ordered:
            raise ValueError("research examples must use canonical chronological order")
        if len({item.id for item in self.examples}) != len(self.examples):
            raise ValueError("research dataset contains duplicate examples")
        if any(item.features.schema_sha256 != self.feature_config.sha256 for item in self.examples):
            raise ValueError("research example uses a different feature schema")
        if self.sha256 != _dataset_hash(
            config=self.config,
            feature_config=self.feature_config,
            feature_set_hashes=self.feature_set_hashes,
            examples=self.examples,
            excluded_neutral_count=self.excluded_neutral_count,
        ):
            raise ValueError("research dataset checksum does not match its contents")

    def to_record(self) -> dict[str, Any]:
        return {
            "config": self.config.to_record(),
            "feature_config": self.feature_config.to_record(),
            "feature_set_hashes": list(self.feature_set_hashes),
            "examples": [item.to_record() for item in self.examples],
            "excluded_neutral_count": self.excluded_neutral_count,
            "sha256": self.sha256,
        }


def _dataset_hash(
    *,
    config: DatasetBuildConfig,
    feature_config: FeaturePipelineConfig,
    feature_set_hashes: tuple[str, ...],
    examples: tuple[LabeledExample, ...],
    excluded_neutral_count: int,
) -> str:
    payload = {
        "config": config.to_record(),
        "feature_config": feature_config.to_record(),
        "feature_set_hashes": list(feature_set_hashes),
        "examples": [item.to_record() for item in examples],
        "excluded_neutral_count": excluded_neutral_count,
    }
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _label_feature_set(
    *,
    candles: tuple[Candle, ...],
    features: FeatureSet,
    config: DatasetBuildConfig,
) -> tuple[tuple[LabeledExample, ...], int]:
    index_by_observed_at = {candle.close_time: index for index, candle in enumerate(candles)}
    examples: list[LabeledExample] = []
    excluded = 0
    for vector in features.vectors:
        source_index = index_by_observed_at[vector.observed_at]
        label_index = source_index + config.horizon_bars
        if label_index >= len(candles):
            continue
        source_close = candles[source_index].close
        future = candles[label_index]
        label_return = (future.close / source_close) - Decimal("1")
        if abs(label_return) <= config.neutral_return_threshold:
            excluded += 1
            continue
        label = DirectionLabel.UP if label_return > 0 else DirectionLabel.DOWN
        identity = _example_identity(
            features=vector,
            label=label,
            label_return=label_return,
            label_end_at=future.close_time,
        )
        examples.append(
            LabeledExample(
                id=identity,
                features=vector,
                label=label,
                label_return=label_return,
                label_end_at=future.close_time,
            )
        )
    return tuple(examples), excluded


def build_research_dataset(
    *,
    candle_series: tuple[tuple[Candle, ...], ...],
    config: DatasetBuildConfig,
    feature_config: FeaturePipelineConfig | None = None,
) -> ResearchDataset:
    """Build a multi-symbol dataset without mixing histories between instruments."""

    if not candle_series:
        raise ValueError("research dataset requires at least one candle series")
    effective_features = feature_config or FeaturePipelineConfig()
    identities: set[tuple[str, str]] = set()
    feature_sets: list[FeatureSet] = []
    examples: list[LabeledExample] = []
    excluded = 0
    for candles in candle_series:
        validate_closed_candle_series(candles)
        identity = (candles[0].symbol, candles[0].timeframe)
        if identity in identities:
            raise ValueError("research dataset contains duplicate symbol/timeframe series")
        identities.add(identity)
        feature_set = build_feature_set(candles=candles, config=effective_features)
        feature_sets.append(feature_set)
        labeled, neutral_count = _label_feature_set(
            candles=candles,
            features=feature_set,
            config=config,
        )
        examples.extend(labeled)
        excluded += neutral_count
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
    hashes = tuple(sorted(feature_set.sha256 for feature_set in feature_sets))
    checksum = _dataset_hash(
        config=config,
        feature_config=effective_features,
        feature_set_hashes=hashes,
        examples=ordered,
        excluded_neutral_count=excluded,
    )
    return ResearchDataset(
        config=config,
        feature_config=effective_features,
        feature_set_hashes=hashes,
        examples=ordered,
        excluded_neutral_count=excluded,
        sha256=checksum,
    )
