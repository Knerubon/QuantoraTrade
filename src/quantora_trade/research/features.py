"""Point-in-time technical features derived only from closed candle history."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from quantora_trade.domain.models import Candle
from quantora_trade.strategy.indicators import IndicatorConfig, calculate_indicators
from quantora_trade.strategy.market_structure import (
    MarketStructureConfig,
    PriceZone,
    StructureKind,
    calculate_market_structure,
)
from quantora_trade.strategy.patterns import (
    CandlestickPattern,
    PatternConfig,
    detect_candlestick_patterns,
)
from quantora_trade.strategy.validation import validate_closed_candle_series

FEATURE_NAMES = (
    "body_atr",
    "doji",
    "ema_fast_gap_atr",
    "ema_mid_gap_atr",
    "ema_slow_gap_atr",
    "hammer",
    "macd_histogram_atr",
    "nearest_resistance_atr",
    "nearest_support_atr",
    "range_atr",
    "resistance_present",
    "return_1",
    "rsi_centered",
    "shooting_star",
    "support_present",
    "bearish_engulfing",
    "bullish_engulfing",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class FeaturePipelineConfig:
    """Every parameter that can change the feature values or warm-up period."""

    version: str = "technical-features-v1"
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    structure: MarketStructureConfig = field(default_factory=MarketStructureConfig)
    patterns: PatternConfig = field(default_factory=PatternConfig)

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("feature pipeline version must not be empty")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "indicators": {key: str(value) for key, value in asdict(self.indicators).items()},
            "structure": {key: str(value) for key, value in asdict(self.structure).items()},
            "patterns": {key: str(value) for key, value in asdict(self.patterns).items()},
            "feature_names": list(FEATURE_NAMES),
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_record()).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """One immutable vector containing only evidence known at ``observed_at``."""

    symbol: str
    timeframe: str
    observed_at: datetime
    source_open_time: datetime
    pipeline_version: str
    schema_sha256: str
    values: tuple[tuple[str, Decimal], ...]

    def __post_init__(self) -> None:
        _require_utc(self.observed_at, "observed_at")
        _require_utc(self.source_open_time, "source_open_time")
        if self.source_open_time >= self.observed_at:
            raise ValueError("feature source must precede its observation time")
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("feature symbol must be canonical uppercase")
        if not self.pipeline_version.strip():
            raise ValueError("feature pipeline version must not be empty")
        if not _SHA256.fullmatch(self.schema_sha256):
            raise ValueError("feature schema checksum must be a SHA-256")
        names = tuple(name for name, _ in self.values)
        if names != FEATURE_NAMES:
            raise ValueError("feature values do not match the canonical schema")
        if any(not value.is_finite() for _, value in self.values):
            raise ValueError("feature values must be finite")

    def value(self, name: str) -> Decimal:
        try:
            return next(value for item_name, value in self.values if item_name == name)
        except StopIteration as error:
            raise KeyError(name) from error

    def to_record(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "observed_at": self.observed_at.isoformat(),
            "source_open_time": self.source_open_time.isoformat(),
            "pipeline_version": self.pipeline_version,
            "schema_sha256": self.schema_sha256,
            "values": {name: str(value) for name, value in self.values},
        }


@dataclass(frozen=True, slots=True)
class FeatureSet:
    config: FeaturePipelineConfig
    vectors: tuple[FeatureVector, ...]
    sha256: str

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(self.vectors, key=lambda item: (item.observed_at, item.symbol, item.timeframe))
        )
        if self.vectors != ordered:
            raise ValueError("feature vectors must use canonical chronological order")
        identities = tuple((item.symbol, item.timeframe, item.observed_at) for item in self.vectors)
        if len(identities) != len(set(identities)):
            raise ValueError("feature set contains duplicate point-in-time identities")
        if any(
            vector.pipeline_version != self.config.version
            or vector.schema_sha256 != self.config.sha256
            for vector in self.vectors
        ):
            raise ValueError("feature vector schema does not match the feature set")
        if self.sha256 != _feature_set_hash(self.config, self.vectors):
            raise ValueError("feature set checksum does not match its contents")

    def to_records(self) -> tuple[dict[str, Any], ...]:
        return tuple(vector.to_record() for vector in self.vectors)


def _feature_set_hash(config: FeaturePipelineConfig, vectors: tuple[FeatureVector, ...]) -> str:
    payload = {"config": config.to_record(), "vectors": [item.to_record() for item in vectors]}
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _nearest_zone_distance(
    *, close: Decimal, atr: Decimal, zones: tuple[PriceZone, ...], kind: StructureKind
) -> tuple[Decimal, Decimal]:
    candidates = tuple(zone.midpoint for zone in zones if zone.kind is kind)
    if not candidates:
        return Decimal("0"), Decimal("0")
    if kind is StructureKind.SUPPORT:
        eligible = tuple(price for price in candidates if price <= close)
        if not eligible:
            return Decimal("0"), Decimal("0")
        return (close - max(eligible)) / atr, Decimal("1")
    eligible = tuple(price for price in candidates if price >= close)
    if not eligible:
        return Decimal("0"), Decimal("0")
    return (min(eligible) - close) / atr, Decimal("1")


def build_feature_set(
    *, candles: tuple[Candle, ...], config: FeaturePipelineConfig | None = None
) -> FeatureSet:
    """Build causal features, omitting only the deterministic indicator warm-up rows."""

    validate_closed_candle_series(candles)
    effective = config or FeaturePipelineConfig()
    indicators = calculate_indicators(candles, effective.indicators)
    structures = calculate_market_structure(candles, effective.structure)
    patterns = detect_candlestick_patterns(candles, effective.patterns)
    vectors: list[FeatureVector] = []
    for index, (candle, indicator, structure, pattern) in enumerate(
        zip(candles, indicators, structures, patterns, strict=True)
    ):
        required = (
            indicator.ema_fast,
            indicator.ema_mid,
            indicator.ema_slow,
            indicator.rsi,
            indicator.macd_histogram,
            indicator.atr,
        )
        if index == 0 or any(value is None for value in required):
            continue
        ema_fast, ema_mid, ema_slow, rsi, macd_histogram, atr = required
        assert all(value is not None for value in required)
        assert ema_fast is not None
        assert ema_mid is not None
        assert ema_slow is not None
        assert rsi is not None
        assert macd_histogram is not None
        assert atr is not None
        if atr <= 0 or candles[index - 1].close <= 0:
            continue
        support_distance, support_present = _nearest_zone_distance(
            close=candle.close,
            atr=atr,
            zones=structure.zones,
            kind=StructureKind.SUPPORT,
        )
        resistance_distance, resistance_present = _nearest_zone_distance(
            close=candle.close,
            atr=atr,
            zones=structure.zones,
            kind=StructureKind.RESISTANCE,
        )
        detected = set(pattern.patterns)
        values = {
            "body_atr": (candle.close - candle.open) / atr,
            "doji": Decimal(CandlestickPattern.DOJI in detected),
            "ema_fast_gap_atr": (candle.close - ema_fast) / atr,
            "ema_mid_gap_atr": (candle.close - ema_mid) / atr,
            "ema_slow_gap_atr": (candle.close - ema_slow) / atr,
            "hammer": Decimal(CandlestickPattern.HAMMER in detected),
            "macd_histogram_atr": macd_histogram / atr,
            "nearest_resistance_atr": resistance_distance,
            "nearest_support_atr": support_distance,
            "range_atr": (candle.high - candle.low) / atr,
            "resistance_present": resistance_present,
            "return_1": (candle.close / candles[index - 1].close) - Decimal("1"),
            "rsi_centered": (rsi - Decimal("50")) / Decimal("50"),
            "shooting_star": Decimal(CandlestickPattern.SHOOTING_STAR in detected),
            "support_present": support_present,
            "bearish_engulfing": Decimal(CandlestickPattern.BEARISH_ENGULFING in detected),
            "bullish_engulfing": Decimal(CandlestickPattern.BULLISH_ENGULFING in detected),
        }
        vectors.append(
            FeatureVector(
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                observed_at=candle.close_time,
                source_open_time=candle.open_time,
                pipeline_version=effective.version,
                schema_sha256=effective.sha256,
                values=tuple((name, values[name]) for name in FEATURE_NAMES),
            )
        )
    result = tuple(vectors)
    return FeatureSet(config=effective, vectors=result, sha256=_feature_set_hash(effective, result))
