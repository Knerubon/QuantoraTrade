"""Conservative protective exits for ambiguous OHLC bars."""

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5

from quantora_trade.backtesting.execution import ExecutionCostModel, SimulatedFill
from quantora_trade.backtesting.portfolio import OpenPosition
from quantora_trade.domain.enums import Action
from quantora_trade.domain.models import Candle


class IntrabarExitReason(StrEnum):
    """Stable protective-exit reasons recorded by a backtest."""

    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    GAP_STOP_LOSS = "GAP_STOP_LOSS"
    GAP_TAKE_PROFIT = "GAP_TAKE_PROFIT"


@dataclass(frozen=True, slots=True)
class IntrabarExit:
    """A protective exit and whether OHLC ordering made it ambiguous."""

    fill: SimulatedFill
    reason: IntrabarExitReason
    ambiguous: bool


def _closing_fill(
    *,
    position: OpenPosition,
    candle: Candle,
    reference_price: Decimal,
    reason: IntrabarExitReason,
    costs: ExecutionCostModel,
) -> SimulatedFill:
    side = Action.SELL if position.side is Action.BUY else Action.BUY
    spread_price = costs.point * costs.spread_points
    slippage_price = costs.point * costs.slippage_points
    adverse_cost = (spread_price / Decimal("2")) + slippage_price
    direction = Decimal("1") if side is Action.BUY else Decimal("-1")
    fill_price = reference_price + (direction * adverse_cost)
    identity = json.dumps(
        {
            "candle_open_time": candle.open_time.isoformat(),
            "cost_scenario": costs.scenario,
            "fill_price": str(fill_price),
            "position_id": str(position.id),
            "reason": reason.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    executed_at = (
        candle.open_time
        if reason in {IntrabarExitReason.GAP_STOP_LOSS, IntrabarExitReason.GAP_TAKE_PROFIT}
        else candle.close_time
    )
    return SimulatedFill(
        id=uuid5(NAMESPACE_URL, identity),
        signal_id=position.opening_signal_id,
        symbol=position.symbol,
        side=side,
        executed_at=executed_at,
        reference_price=reference_price,
        fill_price=fill_price,
        spread_price=spread_price,
        slippage_price=slippage_price,
        commission=costs.commission_per_side,
        cost_scenario=costs.scenario,
    )


def simulate_conservative_intrabar_exit(
    *,
    position: OpenPosition,
    candle: Candle,
    costs: ExecutionCostModel,
) -> IntrabarExit | None:
    """Resolve SL/TP using stop-first ordering when one OHLC bar touches both."""

    if not candle.is_closed:
        raise ValueError("intrabar exit requires a closed candle")
    if (candle.symbol, candle.timeframe) != (position.symbol, position.timeframe):
        raise ValueError("intrabar candle identity must match position")
    if candle.open_time < position.opened_at:
        raise ValueError("intrabar candle opens before position entry")
    stop = position.stop_loss
    target = position.take_profit
    if stop is None and target is None:
        return None

    if position.side is Action.BUY:
        if stop is not None and candle.open <= stop:
            reason = IntrabarExitReason.GAP_STOP_LOSS
            reference = candle.open
            return IntrabarExit(
                fill=_closing_fill(
                    position=position,
                    candle=candle,
                    reference_price=reference,
                    reason=reason,
                    costs=costs,
                ),
                reason=reason,
                ambiguous=False,
            )
        if target is not None and candle.open >= target:
            reason = IntrabarExitReason.GAP_TAKE_PROFIT
            return IntrabarExit(
                fill=_closing_fill(
                    position=position,
                    candle=candle,
                    reference_price=target,
                    reason=reason,
                    costs=costs,
                ),
                reason=reason,
                ambiguous=False,
            )
        stop_hit = stop is not None and candle.low <= stop
        target_hit = target is not None and candle.high >= target
    else:
        if stop is not None and candle.open >= stop:
            reason = IntrabarExitReason.GAP_STOP_LOSS
            reference = candle.open
            return IntrabarExit(
                fill=_closing_fill(
                    position=position,
                    candle=candle,
                    reference_price=reference,
                    reason=reason,
                    costs=costs,
                ),
                reason=reason,
                ambiguous=False,
            )
        if target is not None and candle.open <= target:
            reason = IntrabarExitReason.GAP_TAKE_PROFIT
            return IntrabarExit(
                fill=_closing_fill(
                    position=position,
                    candle=candle,
                    reference_price=target,
                    reason=reason,
                    costs=costs,
                ),
                reason=reason,
                ambiguous=False,
            )
        stop_hit = stop is not None and candle.high >= stop
        target_hit = target is not None and candle.low <= target

    if not stop_hit and not target_hit:
        return None
    ambiguous = stop_hit and target_hit
    reason = IntrabarExitReason.STOP_LOSS if stop_hit else IntrabarExitReason.TAKE_PROFIT
    reference_price = stop if stop_hit else target
    if reference_price is None:  # pragma: no cover - narrowed by the hit flags
        raise RuntimeError("intrabar trigger price is missing")
    return IntrabarExit(
        fill=_closing_fill(
            position=position,
            candle=candle,
            reference_price=reference_price,
            reason=reason,
            costs=costs,
        ),
        reason=reason,
        ambiguous=ambiguous,
    )
