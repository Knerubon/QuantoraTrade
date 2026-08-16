"""Tests for chronological dataset partitions with leakage buffers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantora_trade.backtesting.splits import TemporalSample, chronological_split

START = datetime(2025, 1, 1, tzinfo=UTC)


def sample(index: int) -> TemporalSample:
    observed_at = START + timedelta(days=index)
    return TemporalSample(
        id=f"sample-{index}",
        observed_at=observed_at,
        label_end_at=observed_at + timedelta(hours=12),
    )


def test_split_is_chronological_and_excludes_purge_embargo_windows() -> None:
    samples = tuple(sample(index) for index in range(10))

    result = chronological_split(
        samples=samples,
        training_fraction=Decimal("0.5"),
        validation_fraction=Decimal("0.3"),
        purge=timedelta(days=1),
        embargo=timedelta(days=1),
    )

    assert tuple(item.id for item in result.training) == (
        "sample-0",
        "sample-1",
        "sample-2",
        "sample-3",
    )
    assert tuple(item.id for item in result.validation) == ("sample-6",)
    assert tuple(item.id for item in result.test) == ("sample-9",)
    assert tuple(item.id for item in result.excluded) == (
        "sample-4",
        "sample-5",
        "sample-7",
        "sample-8",
    )
    assert max(item.label_end_at for item in result.training) < result.validation_boundary
    assert max(item.label_end_at for item in result.validation) < result.test_boundary


def test_zero_buffers_keep_each_raw_partition() -> None:
    result = chronological_split(
        samples=tuple(sample(index) for index in range(10)),
        training_fraction=Decimal("0.6"),
        validation_fraction=Decimal("0.2"),
    )

    assert len(result.training) == 6
    assert len(result.validation) == 2
    assert len(result.test) == 2
    assert result.excluded == ()


@pytest.mark.parametrize(
    ("samples", "training", "validation", "message"),
    [
        ((sample(0), sample(1)), Decimal("0.5"), Decimal("0.25"), "three samples"),
        (
            (sample(1), sample(0), sample(2)),
            Decimal("0.4"),
            Decimal("0.3"),
            "chronological order",
        ),
        (
            (sample(0), sample(1), sample(2)),
            Decimal("0.8"),
            Decimal("0.2"),
            "leave a test",
        ),
    ],
)
def test_invalid_split_inputs_fail_closed(
    samples: tuple[TemporalSample, ...],
    training: Decimal,
    validation: Decimal,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        chronological_split(
            samples=samples,
            training_fraction=training,
            validation_fraction=validation,
        )


def test_sample_rejects_non_utc_and_backward_label() -> None:
    with pytest.raises(ValueError, match="UTC"):
        TemporalSample(id="bad", observed_at=datetime(2025, 1, 1), label_end_at=START)
    with pytest.raises(ValueError, match="before observation"):
        TemporalSample(
            id="bad",
            observed_at=START,
            label_end_at=START - timedelta(seconds=1),
        )
