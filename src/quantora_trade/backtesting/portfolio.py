"""Immutable multi-symbol position and portfolio accounting for backtests."""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from quantora_trade.backtesting.execution import SimulatedFill
from quantora_trade.domain.enums import Action
from quantora_trade.domain.models import Instrument


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class OpenPosition:
    """One marked position with the broker tick economics fixed at entry."""

    id: UUID
    opening_fill_id: UUID
    opening_signal_id: UUID
    symbol: str
    timeframe: str
    side: Action
    volume: Decimal
    entry_reference_price: Decimal
    entry_price: Decimal
    opened_at: datetime
    tick_size: Decimal
    tick_value: Decimal
    entry_commission: Decimal
    entry_spread_price: Decimal
    entry_slippage_price: Decimal
    mark_price: Decimal
    marked_at: datetime
    stop_loss: Decimal | None
    take_profit: Decimal | None
    margin_required: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("position symbol must be canonical uppercase")
        if self.timeframe not in {"M5", "M15", "H1"}:
            raise ValueError("position timeframe is not supported")
        if not isinstance(self.side, Action) or self.side is Action.HOLD:
            raise ValueError("position side must be BUY or SELL")
        _require_utc(self.opened_at, "opened_at")
        _require_utc(self.marked_at, "marked_at")
        if self.marked_at < self.opened_at:
            raise ValueError("position mark cannot precede entry")
        economics = (
            self.volume,
            self.entry_reference_price,
            self.entry_price,
            self.tick_size,
            self.tick_value,
            self.entry_commission,
            self.entry_spread_price,
            self.entry_slippage_price,
            self.mark_price,
            self.margin_required,
        )
        if any(not value.is_finite() for value in economics):
            raise ValueError("position economics must be finite")
        if (
            min(
                self.volume,
                self.entry_reference_price,
                self.entry_price,
                self.tick_size,
                self.tick_value,
                self.mark_price,
            )
            <= 0
        ):
            raise ValueError("position economics must be greater than zero")
        if (
            min(
                self.entry_commission,
                self.entry_spread_price,
                self.entry_slippage_price,
                self.margin_required,
            )
            < 0
        ):
            raise ValueError("entry costs must be non-negative")
        protective_prices = tuple(
            price for price in (self.stop_loss, self.take_profit) if price is not None
        )
        if any(not price.is_finite() or price <= 0 for price in protective_prices):
            raise ValueError("protective prices must be finite and greater than zero")
        if self.side is Action.BUY:
            if self.stop_loss is not None and self.stop_loss >= self.entry_price:
                raise ValueError("BUY stop loss must be below entry")
            if self.take_profit is not None and self.take_profit <= self.entry_price:
                raise ValueError("BUY take profit must be above entry")
        if self.side is Action.SELL:
            if self.stop_loss is not None and self.stop_loss <= self.entry_price:
                raise ValueError("SELL stop loss must be above entry")
            if self.take_profit is not None and self.take_profit >= self.entry_price:
                raise ValueError("SELL take profit must be below entry")

    @property
    def unrealized_pnl(self) -> Decimal:
        direction = Decimal("1") if self.side is Action.BUY else Decimal("-1")
        ticks = ((self.mark_price - self.entry_price) / self.tick_size) * direction
        return ticks * self.tick_value * self.volume


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """Auditable realized result including both sides of commission."""

    position_id: UUID
    opening_fill_id: UUID
    closing_fill_id: UUID
    opening_signal_id: UUID
    symbol: str
    timeframe: str
    side: Action
    volume: Decimal
    entry_reference_price: Decimal
    exit_reference_price: Decimal
    entry_price: Decimal
    exit_price: Decimal
    opened_at: datetime
    closed_at: datetime
    gross_pnl: Decimal
    execution_cost: Decimal
    entry_commission: Decimal
    exit_commission: Decimal
    net_pnl: Decimal
    swap_cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.symbol != self.symbol.strip().upper():
            raise ValueError("closed trade symbol must be canonical uppercase")
        if self.timeframe not in {"M5", "M15", "H1"}:
            raise ValueError("closed trade timeframe is not supported")
        if not isinstance(self.side, Action) or self.side is Action.HOLD:
            raise ValueError("closed trade side must be BUY or SELL")
        _require_utc(self.opened_at, "opened_at")
        _require_utc(self.closed_at, "closed_at")
        if self.closed_at < self.opened_at:
            raise ValueError("closed trade cannot end before it opens")
        values = (
            self.volume,
            self.entry_reference_price,
            self.exit_reference_price,
            self.entry_price,
            self.exit_price,
            self.gross_pnl,
            self.execution_cost,
            self.entry_commission,
            self.exit_commission,
            self.net_pnl,
            self.swap_cost,
        )
        if any(not value.is_finite() for value in values):
            raise ValueError("closed trade values must be finite")
        if (
            self.volume <= 0
            or self.entry_reference_price <= 0
            or self.exit_reference_price <= 0
            or self.entry_price <= 0
            or self.exit_price <= 0
        ):
            raise ValueError("closed trade volume and prices must be greater than zero")
        if (
            min(
                self.execution_cost,
                self.entry_commission,
                self.exit_commission,
                self.swap_cost,
            )
            < 0
        ):
            raise ValueError("closed trade costs must be non-negative")
        if (
            self.net_pnl
            != self.gross_pnl
            - self.execution_cost
            - self.entry_commission
            - self.exit_commission
            - self.swap_cost
        ):
            raise ValueError("closed trade net PnL must reconcile with costs")


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Persistent balance, equity, and position state across multiple symbols."""

    cash_balance: Decimal
    realized_pnl: Decimal = Decimal("0")
    positions: tuple[OpenPosition, ...] = ()
    closed_trades: tuple[ClosedTrade, ...] = ()

    def __post_init__(self) -> None:
        if not self.cash_balance.is_finite() or self.cash_balance < 0:
            raise ValueError("cash balance must be a finite non-negative value")
        if not self.realized_pnl.is_finite():
            raise ValueError("realized PnL must be finite")
        position_ids = tuple(position.id for position in self.positions)
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("portfolio contains duplicate position IDs")

    @property
    def unrealized_pnl(self) -> Decimal:
        return sum((position.unrealized_pnl for position in self.positions), Decimal("0"))

    @property
    def equity(self) -> Decimal:
        return self.cash_balance + self.unrealized_pnl

    @property
    def margin_used(self) -> Decimal:
        return sum((position.margin_required for position in self.positions), Decimal("0"))

    @property
    def free_margin(self) -> Decimal:
        return self.equity - self.margin_used

    def open_position(
        self,
        *,
        fill: SimulatedFill,
        volume: Decimal,
        instrument: Instrument,
        timeframe: str = "M15",
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        margin_required: Decimal = Decimal("0"),
    ) -> "PortfolioState":
        """Open a position after validating symbol and broker volume constraints."""

        if fill.symbol != instrument.symbol:
            raise ValueError("fill and instrument symbols must match")
        if not volume.is_finite():
            raise ValueError("position volume must be finite")
        if not instrument.volume_min <= volume <= instrument.volume_max:
            raise ValueError("position volume is outside instrument limits")
        if (volume - instrument.volume_min) % instrument.volume_step != 0:
            raise ValueError("position volume is not aligned to instrument step")
        if fill.commission > self.cash_balance:
            raise ValueError("insufficient cash for entry commission")
        if not margin_required.is_finite() or margin_required < 0:
            raise ValueError("required margin must be finite and non-negative")
        if margin_required > self.free_margin - fill.commission:
            raise ValueError("insufficient free margin")
        identity = json.dumps(
            {"fill_id": str(fill.id), "symbol": fill.symbol, "volume": str(volume)},
            sort_keys=True,
            separators=(",", ":"),
        )
        position = OpenPosition(
            id=uuid5(NAMESPACE_URL, identity),
            opening_fill_id=fill.id,
            opening_signal_id=fill.signal_id,
            symbol=fill.symbol,
            timeframe=timeframe,
            side=fill.side,
            volume=volume,
            entry_reference_price=fill.reference_price,
            entry_price=fill.fill_price,
            opened_at=fill.executed_at,
            tick_size=instrument.tick_size,
            tick_value=instrument.tick_value,
            entry_commission=fill.commission,
            entry_spread_price=fill.spread_price,
            entry_slippage_price=fill.slippage_price,
            mark_price=fill.fill_price,
            marked_at=fill.executed_at,
            stop_loss=stop_loss,
            take_profit=take_profit,
            margin_required=margin_required,
        )
        if any(existing.id == position.id for existing in self.positions):
            raise ValueError("opening fill has already created a position")
        return PortfolioState(
            cash_balance=self.cash_balance - fill.commission,
            realized_pnl=self.realized_pnl - fill.commission,
            positions=(*self.positions, position),
            closed_trades=self.closed_trades,
        )

    def mark_to_market(
        self,
        *,
        symbol: str,
        price: Decimal,
        observed_at: datetime,
        timeframe: str | None = None,
    ) -> "PortfolioState":
        """Mark every open position for one symbol without affecting other symbols."""

        _require_utc(observed_at, "observed_at")
        if price <= 0:
            raise ValueError("mark price must be greater than zero")

        def matches(position: OpenPosition) -> bool:
            return position.symbol == symbol and (
                timeframe is None or position.timeframe == timeframe
            )

        if not any(matches(position) for position in self.positions):
            raise ValueError("portfolio has no open position for symbol")
        updated: list[OpenPosition] = []
        for position in self.positions:
            if not matches(position):
                updated.append(position)
                continue
            if observed_at < position.marked_at:
                raise ValueError("portfolio mark cannot move backward in time")
            updated.append(replace(position, mark_price=price, marked_at=observed_at))
        return replace(self, positions=tuple(updated))

    def close_position(
        self,
        *,
        position_id: UUID,
        fill: SimulatedFill,
        swap_cost: Decimal = Decimal("0"),
    ) -> "PortfolioState":
        """Close exactly one position and realize tick-value PnL plus exit commission."""

        matches = [position for position in self.positions if position.id == position_id]
        if not matches:
            raise ValueError("position does not exist")
        position = matches[0]
        if fill.symbol != position.symbol:
            raise ValueError("closing fill symbol does not match position")
        expected_side = Action.SELL if position.side is Action.BUY else Action.BUY
        if fill.side is not expected_side:
            raise ValueError("closing fill must oppose the position side")
        if fill.executed_at < position.opened_at:
            raise ValueError("closing fill cannot precede position entry")
        if not swap_cost.is_finite() or swap_cost < 0:
            raise ValueError("swap cost must be finite and non-negative")
        direction = Decimal("1") if position.side is Action.BUY else Decimal("-1")
        reference_ticks = (
            (fill.reference_price - position.entry_reference_price) / position.tick_size
        ) * direction
        gross_pnl = reference_ticks * position.tick_value * position.volume
        filled_ticks = ((fill.fill_price - position.entry_price) / position.tick_size) * direction
        filled_pnl = filled_ticks * position.tick_value * position.volume
        execution_cost = gross_pnl - filled_pnl
        if execution_cost < 0:
            raise ValueError("closing fill improves on reference prices")
        net_pnl = (
            gross_pnl - execution_cost - position.entry_commission - fill.commission - swap_cost
        )
        trade = ClosedTrade(
            position_id=position.id,
            opening_fill_id=position.opening_fill_id,
            closing_fill_id=fill.id,
            opening_signal_id=position.opening_signal_id,
            symbol=position.symbol,
            timeframe=position.timeframe,
            side=position.side,
            volume=position.volume,
            entry_reference_price=position.entry_reference_price,
            exit_reference_price=fill.reference_price,
            entry_price=position.entry_price,
            exit_price=fill.fill_price,
            opened_at=position.opened_at,
            closed_at=fill.executed_at,
            gross_pnl=gross_pnl,
            execution_cost=execution_cost,
            entry_commission=position.entry_commission,
            exit_commission=fill.commission,
            net_pnl=net_pnl,
            swap_cost=swap_cost,
        )
        remaining = tuple(item for item in self.positions if item.id != position_id)
        return PortfolioState(
            cash_balance=self.cash_balance + filled_pnl - fill.commission - swap_cost,
            realized_pnl=self.realized_pnl + filled_pnl - fill.commission - swap_cost,
            positions=remaining,
            closed_trades=(*self.closed_trades, trade),
        )
