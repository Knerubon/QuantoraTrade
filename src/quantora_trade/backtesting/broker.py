"""Deterministic broker constraints for backtest fills, margin, and financing."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum

from quantora_trade.domain.enums import Action
from quantora_trade.domain.models import Instrument


class FillStatus(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


class FillReason(StrEnum):
    BELOW_MINIMUM_VOLUME = "BELOW_MINIMUM_VOLUME"
    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"
    LIQUIDITY_LIMIT = "LIQUIDITY_LIMIT"
    PARTIAL_FILL_DISABLED = "PARTIAL_FILL_DISABLED"
    VOLUME_LIMIT = "VOLUME_LIMIT"
    VOLUME_ROUNDED = "VOLUME_ROUNDED"


@dataclass(frozen=True, slots=True)
class BrokerSimulationModel:
    """Per-symbol broker assumptions expressed in account currency."""

    margin_per_lot: Decimal = Decimal("0")
    liquidity_cap: Decimal | None = None
    allow_partial_fills: bool = True
    long_swap_cost_per_lot: Decimal = Decimal("0")
    short_swap_cost_per_lot: Decimal = Decimal("0")
    triple_swap_weekday: int = 2

    def __post_init__(self) -> None:
        values = (
            self.margin_per_lot,
            self.long_swap_cost_per_lot,
            self.short_swap_cost_per_lot,
        )
        if any(not value.is_finite() or value < 0 for value in values):
            raise ValueError("broker monetary assumptions must be finite and non-negative")
        if self.liquidity_cap is not None and (
            not self.liquidity_cap.is_finite() or self.liquidity_cap <= 0
        ):
            raise ValueError("liquidity cap must be finite and greater than zero")
        if not 0 <= self.triple_swap_weekday <= 4:
            raise ValueError("triple swap weekday must be a weekday")


@dataclass(frozen=True, slots=True)
class BrokerFillDecision:
    """Auditable volume decision before a simulated market fill is booked."""

    status: FillStatus
    requested_volume: Decimal
    filled_volume: Decimal
    remaining_volume: Decimal
    margin_required: Decimal
    reason_codes: tuple[FillReason, ...]

    def __post_init__(self) -> None:
        values = (
            self.requested_volume,
            self.filled_volume,
            self.remaining_volume,
            self.margin_required,
        )
        if any(not value.is_finite() or value < 0 for value in values):
            raise ValueError("fill decision values must be finite and non-negative")
        if self.requested_volume <= 0:
            raise ValueError("requested volume must be greater than zero")
        if self.filled_volume + self.remaining_volume != self.requested_volume:
            raise ValueError("fill decision volumes must reconcile")
        if tuple(sorted(set(self.reason_codes), key=str)) != self.reason_codes:
            raise ValueError("fill reason codes must be unique and sorted")
        if self.status is FillStatus.FULL and (self.remaining_volume != 0 or self.reason_codes):
            raise ValueError("full fill cannot have remaining volume or reasons")
        if self.status is FillStatus.PARTIAL and (
            self.filled_volume <= 0 or self.remaining_volume <= 0
        ):
            raise ValueError("partial fill requires filled and remaining volume")
        if self.status is FillStatus.REJECTED and self.filled_volume != 0:
            raise ValueError("rejected fill cannot have filled volume")


def _round_volume_down(*, volume: Decimal, instrument: Instrument) -> Decimal:
    if volume < instrument.volume_min:
        return Decimal("0")
    steps = ((volume - instrument.volume_min) / instrument.volume_step).to_integral_value(
        rounding=ROUND_FLOOR
    )
    return instrument.volume_min + steps * instrument.volume_step


def simulate_broker_fill_decision(
    *,
    requested_volume: Decimal,
    available_margin: Decimal,
    instrument: Instrument,
    model: BrokerSimulationModel,
    commission_per_lot: Decimal = Decimal("0"),
) -> BrokerFillDecision:
    """Apply volume grid, liquidity, and free-margin constraints conservatively."""

    if not requested_volume.is_finite() or requested_volume <= 0:
        raise ValueError("requested volume must be finite and greater than zero")
    if not available_margin.is_finite() or available_margin < 0:
        raise ValueError("available margin must be finite and non-negative")
    if not commission_per_lot.is_finite() or commission_per_lot < 0:
        raise ValueError("commission per lot must be finite and non-negative")

    reasons: set[FillReason] = set()
    capped = requested_volume
    if capped > instrument.volume_max:
        capped = instrument.volume_max
        reasons.add(FillReason.VOLUME_LIMIT)
    if model.liquidity_cap is not None and capped > model.liquidity_cap:
        capped = model.liquidity_cap
        reasons.add(FillReason.LIQUIDITY_LIMIT)
    capital_per_lot = model.margin_per_lot + commission_per_lot
    if capital_per_lot > 0:
        affordable = available_margin / capital_per_lot
        if affordable < capped:
            capped = affordable
            reasons.add(FillReason.INSUFFICIENT_MARGIN)

    filled = _round_volume_down(volume=capped, instrument=instrument)
    if filled == 0:
        reasons.add(FillReason.BELOW_MINIMUM_VOLUME)
    elif filled < capped:
        reasons.add(FillReason.VOLUME_ROUNDED)
    if filled < requested_volume and not model.allow_partial_fills:
        filled = Decimal("0")
        reasons.add(FillReason.PARTIAL_FILL_DISABLED)
    remaining = requested_volume - filled
    ordered_reasons = tuple(sorted(reasons, key=str))
    if filled == requested_volume:
        status = FillStatus.FULL
        ordered_reasons = ()
    elif filled > 0:
        status = FillStatus.PARTIAL
    else:
        status = FillStatus.REJECTED
    return BrokerFillDecision(
        status=status,
        requested_volume=requested_volume,
        filled_volume=filled,
        remaining_volume=remaining,
        margin_required=model.margin_per_lot * filled,
        reason_codes=ordered_reasons,
    )


def calculate_swap_cost(
    *,
    side: Action,
    volume: Decimal,
    opened_at: datetime,
    closed_at: datetime,
    model: BrokerSimulationModel,
) -> Decimal:
    """Charge weekday rollovers, with an explicit triple-swap weekday."""

    if side not in {Action.BUY, Action.SELL}:
        raise ValueError("swap side must be BUY or SELL")
    if not volume.is_finite() or volume <= 0:
        raise ValueError("swap volume must be finite and greater than zero")
    for field_name, value in (("opened_at", opened_at), ("closed_at", closed_at)):
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError(f"{field_name} must be timezone-aware UTC")
    if closed_at < opened_at:
        raise ValueError("swap close time cannot precede open time")

    daily_cost = (
        model.long_swap_cost_per_lot if side is Action.BUY else model.short_swap_cost_per_lot
    )
    cursor = opened_at.date()
    final_date = closed_at.date()
    units = 0
    while cursor < final_date:
        if cursor.weekday() < 5:
            units += 3 if cursor.weekday() == model.triple_swap_weekday else 1
        cursor += timedelta(days=1)
    return daily_cost * volume * units
