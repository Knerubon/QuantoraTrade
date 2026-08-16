"""Chronological dataset splits with explicit purge and embargo controls."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class TemporalSample:
    """Point-in-time sample with the end of its forward-looking label window."""

    id: str
    observed_at: datetime
    label_end_at: datetime

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("sample ID must not be empty")
        _require_utc(self.observed_at, "observed_at")
        _require_utc(self.label_end_at, "label_end_at")
        if self.label_end_at < self.observed_at:
            raise ValueError("sample label cannot end before observation")


@dataclass(frozen=True, slots=True)
class ChronologicalDatasetSplit:
    """Immutable train/validation/test membership plus excluded leakage buffers."""

    training: tuple[TemporalSample, ...]
    validation: tuple[TemporalSample, ...]
    test: tuple[TemporalSample, ...]
    excluded: tuple[TemporalSample, ...]
    validation_boundary: datetime
    test_boundary: datetime
    purge: timedelta
    embargo: timedelta

    def __post_init__(self) -> None:
        _require_utc(self.validation_boundary, "validation_boundary")
        _require_utc(self.test_boundary, "test_boundary")
        if self.validation_boundary >= self.test_boundary:
            raise ValueError("validation boundary must precede test boundary")
        if self.purge < timedelta(0) or self.embargo < timedelta(0):
            raise ValueError("purge and embargo must be non-negative")
        groups = (self.training, self.validation, self.test, self.excluded)
        identifiers = tuple(sample.id for group in groups for sample in group)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("dataset split contains duplicate sample membership")
        if not self.training or not self.validation or not self.test:
            raise ValueError("every dataset partition must contain at least one sample")
        for group in groups:
            if group != tuple(sorted(group, key=lambda sample: (sample.observed_at, sample.id))):
                raise ValueError("dataset partitions must be chronologically ordered")
        if any(
            sample.observed_at >= self.validation_boundary
            or sample.label_end_at > self.validation_boundary - self.purge
            for sample in self.training
        ):
            raise ValueError("training partition crosses the validation boundary")
        if any(
            sample.observed_at < self.validation_boundary + self.embargo
            or sample.observed_at >= self.test_boundary
            or sample.label_end_at > self.test_boundary - self.purge
            for sample in self.validation
        ):
            raise ValueError("validation partition violates a leakage boundary")
        if any(sample.observed_at < self.test_boundary + self.embargo for sample in self.test):
            raise ValueError("test partition violates its embargo boundary")


def chronological_split(
    *,
    samples: tuple[TemporalSample, ...],
    training_fraction: Decimal,
    validation_fraction: Decimal,
    purge: timedelta = timedelta(0),
    embargo: timedelta = timedelta(0),
) -> ChronologicalDatasetSplit:
    """Split ordered samples while removing label overlap and boundary embargoes."""

    if len(samples) < 3:
        raise ValueError("at least three samples are required")
    if len({sample.id for sample in samples}) != len(samples):
        raise ValueError("sample IDs must be unique")
    if samples != tuple(sorted(samples, key=lambda sample: (sample.observed_at, sample.id))):
        raise ValueError("samples must be in deterministic chronological order")
    for name, value in (
        ("training_fraction", training_fraction),
        ("validation_fraction", validation_fraction),
    ):
        if not value.is_finite() or not Decimal("0") < value < Decimal("1"):
            raise ValueError(f"{name} must be between zero and one")
    if training_fraction + validation_fraction >= Decimal("1"):
        raise ValueError("training and validation fractions must leave a test partition")
    if purge < timedelta(0) or embargo < timedelta(0):
        raise ValueError("purge and embargo must be non-negative")

    training_end = int(Decimal(len(samples)) * training_fraction)
    validation_end = int(Decimal(len(samples)) * (training_fraction + validation_fraction))
    if training_end == 0 or validation_end <= training_end or validation_end >= len(samples):
        raise ValueError("fractions produce an empty raw partition")

    validation_boundary = samples[training_end].observed_at
    test_boundary = samples[validation_end].observed_at
    training = tuple(
        sample
        for sample in samples
        if sample.observed_at < validation_boundary
        and sample.label_end_at <= validation_boundary - purge
    )
    validation = tuple(
        sample
        for sample in samples
        if validation_boundary + embargo <= sample.observed_at < test_boundary
        and sample.label_end_at <= test_boundary - purge
    )
    test = tuple(sample for sample in samples if sample.observed_at >= test_boundary + embargo)
    included_ids = {sample.id for group in (training, validation, test) for sample in group}
    excluded = tuple(sample for sample in samples if sample.id not in included_ids)
    return ChronologicalDatasetSplit(
        training=training,
        validation=validation,
        test=test,
        excluded=excluded,
        validation_boundary=validation_boundary,
        test_boundary=test_boundary,
        purge=purge,
        embargo=embargo,
    )
