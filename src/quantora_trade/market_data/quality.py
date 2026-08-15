"""Deterministic validation for normalized candle sequences."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from quantora_trade.domain.models import Candle


class DataQualitySeverity(StrEnum):
    """Severity used to decide whether analysis may continue."""

    WARNING = "warning"
    ERROR = "error"


class DataQualityIssueCode(StrEnum):
    """Stable issue codes for logs, reports, and policy decisions."""

    EMPTY_DATA = "EMPTY_DATA"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    TIMEFRAME_MISMATCH = "TIMEFRAME_MISMATCH"
    FORMING_CANDLE = "FORMING_CANDLE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    DUPLICATE_CANDLE = "DUPLICATE_CANDLE"
    GAP_DETECTED = "GAP_DETECTED"
    STALE_DATA = "STALE_DATA"


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    """One deterministic validation finding."""

    code: DataQualityIssueCode
    severity: DataQualitySeverity
    message: str
    candle_open_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    """Complete validation result for a symbol and timeframe."""

    symbol: str
    timeframe: str
    candle_count: int
    issues: tuple[DataQualityIssue, ...]

    @property
    def is_usable(self) -> bool:
        return not any(issue.severity is DataQualitySeverity.ERROR for issue in self.issues)


@dataclass(frozen=True, slots=True)
class MarketDataValidator:
    """Validates identity, ordering, completeness, and freshness."""

    expected_interval: timedelta
    max_staleness: timedelta
    gap_multiplier: int = 1

    def validate(
        self,
        candles: tuple[Candle, ...],
        *,
        symbol: str,
        timeframe: str,
        as_of: datetime,
    ) -> DataQualityReport:
        issues: list[DataQualityIssue] = []
        if not candles:
            issues.append(
                DataQualityIssue(
                    code=DataQualityIssueCode.EMPTY_DATA,
                    severity=DataQualitySeverity.ERROR,
                    message="No candles were returned.",
                )
            )
            return DataQualityReport(symbol, timeframe, 0, tuple(issues))

        previous: Candle | None = None
        seen_open_times: set[datetime] = set()
        for candle in candles:
            if candle.symbol != symbol:
                issues.append(
                    DataQualityIssue(
                        DataQualityIssueCode.SYMBOL_MISMATCH,
                        DataQualitySeverity.ERROR,
                        f"Expected {symbol}, received {candle.symbol}.",
                        candle.open_time,
                    )
                )
            if candle.timeframe != timeframe:
                issues.append(
                    DataQualityIssue(
                        DataQualityIssueCode.TIMEFRAME_MISMATCH,
                        DataQualitySeverity.ERROR,
                        f"Expected {timeframe}, received {candle.timeframe}.",
                        candle.open_time,
                    )
                )
            if not candle.is_closed:
                issues.append(
                    DataQualityIssue(
                        DataQualityIssueCode.FORMING_CANDLE,
                        DataQualitySeverity.ERROR,
                        "Forming candles are not safe for closed-bar analysis.",
                        candle.open_time,
                    )
                )
            if candle.open_time in seen_open_times:
                issues.append(
                    DataQualityIssue(
                        DataQualityIssueCode.DUPLICATE_CANDLE,
                        DataQualitySeverity.ERROR,
                        "Duplicate candle open time.",
                        candle.open_time,
                    )
                )
            seen_open_times.add(candle.open_time)

            if previous is not None:
                if candle.open_time < previous.open_time:
                    issues.append(
                        DataQualityIssue(
                            DataQualityIssueCode.OUT_OF_ORDER,
                            DataQualitySeverity.ERROR,
                            "Candles are not ordered by open time.",
                            candle.open_time,
                        )
                    )
                expected_max_gap = self.expected_interval * self.gap_multiplier
                if candle.open_time - previous.open_time > expected_max_gap:
                    issues.append(
                        DataQualityIssue(
                            DataQualityIssueCode.GAP_DETECTED,
                            DataQualitySeverity.WARNING,
                            "A gap was detected; session/calendar validation is required.",
                            candle.open_time,
                        )
                    )
            previous = candle

        latest = candles[-1]
        if as_of - latest.close_time > self.max_staleness:
            issues.append(
                DataQualityIssue(
                    DataQualityIssueCode.STALE_DATA,
                    DataQualitySeverity.ERROR,
                    "Latest closed candle is stale.",
                    latest.open_time,
                )
            )

        return DataQualityReport(symbol, timeframe, len(candles), tuple(issues))
