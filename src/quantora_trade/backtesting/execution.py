"""Conservative next-bar market execution for deterministic backtests."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from quantora_trade.domain.enums import Action
from quantora_trade.domain.models import Candle, Signal


@dataclass(frozen=True, slots=True)
class ExecutionCostModel:
    """Explicit per-symbol cost assumptions for one execution scenario."""

    point: Decimal
    spread_points: Decimal
    slippage_points: Decimal
    commission_per_side: Decimal
    scenario: str = "base"

    def __post_init__(self) -> None:
        values = (self.point, self.spread_points, self.slippage_points, self.commission_per_side)
        if any(not value.is_finite() for value in values):
            raise ValueError("execution cost values must be finite")
        if self.point <= 0:
            raise ValueError("point must be greater than zero")
        for field_name in ("spread_points", "slippage_points", "commission_per_side"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if not self.scenario.strip():
            raise ValueError("cost scenario must not be empty")


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    """Auditable fill generated at the next bar open, never the signal bar close."""

    id: UUID
    signal_id: UUID
    symbol: str
    side: Action
    executed_at: datetime
    reference_price: Decimal
    fill_price: Decimal
    spread_price: Decimal
    slippage_price: Decimal
    commission: Decimal
    cost_scenario: str

    def __post_init__(self) -> None:
        if self.executed_at.tzinfo is None or self.executed_at.utcoffset() != UTC.utcoffset(
            self.executed_at
        ):
            raise ValueError("executed_at must be timezone-aware UTC")
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("fill symbol must be canonical uppercase")
        if not isinstance(self.side, Action) or self.side is Action.HOLD:
            raise ValueError("fill side must be BUY or SELL")
        values = (
            self.reference_price,
            self.fill_price,
            self.spread_price,
            self.slippage_price,
            self.commission,
        )
        if any(not value.is_finite() for value in values):
            raise ValueError("fill prices and costs must be finite")
        if self.reference_price <= 0 or self.fill_price <= 0:
            raise ValueError("fill prices must be greater than zero")
        if min(self.spread_price, self.slippage_price, self.commission) < 0:
            raise ValueError("fill costs must be non-negative")
        if not self.cost_scenario.strip():
            raise ValueError("fill cost scenario must not be empty")


def simulate_next_bar_market_fill(
    *,
    signal: Signal,
    next_bar: Candle,
    costs: ExecutionCostModel,
) -> SimulatedFill:
    """Fill a non-HOLD signal at the next bar open with adverse spread and slippage."""

    if signal.action is Action.HOLD:
        raise ValueError("HOLD signal cannot be executed")
    if not next_bar.is_closed:
        raise ValueError("historical execution requires a closed next bar")
    if (next_bar.symbol, next_bar.timeframe) != (signal.symbol, signal.timeframe):
        raise ValueError("next bar identity must match signal symbol and timeframe")
    if next_bar.open_time < signal.observed_at:
        raise ValueError("next bar opens before the signal was observed")
    if next_bar.open_time > signal.expires_at:
        raise ValueError("signal expired before the next bar opened")

    spread_price = costs.point * costs.spread_points
    slippage_price = costs.point * costs.slippage_points
    adverse_cost = (spread_price / Decimal("2")) + slippage_price
    direction = Decimal("1") if signal.action is Action.BUY else Decimal("-1")
    fill_price = next_bar.open + (direction * adverse_cost)
    identity = json.dumps(
        {
            "cost_scenario": costs.scenario,
            "executed_at": next_bar.open_time.isoformat(),
            "fill_price": str(fill_price),
            "signal_id": str(signal.id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return SimulatedFill(
        id=uuid5(NAMESPACE_URL, identity),
        signal_id=signal.id,
        symbol=signal.symbol,
        side=signal.action,
        executed_at=next_bar.open_time,
        reference_price=next_bar.open,
        fill_price=fill_price,
        spread_price=spread_price,
        slippage_price=slippage_price,
        commission=costs.commission_per_side,
        cost_scenario=costs.scenario,
    )
