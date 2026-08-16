"""Immutable orchestration of clock, pending signals, fills, exits, and accounting."""

from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from quantora_trade.backtesting.clock import CandleEvent, SimulationClock
from quantora_trade.backtesting.execution import (
    ExecutionCostModel,
    SimulatedFill,
    simulate_next_bar_market_fill,
)
from quantora_trade.backtesting.intrabar import IntrabarExit, simulate_conservative_intrabar_exit
from quantora_trade.backtesting.portfolio import PortfolioState
from quantora_trade.domain.enums import Action
from quantora_trade.domain.models import Candle, Instrument, Signal


@dataclass(frozen=True, slots=True)
class PendingOrder:
    """A sized signal waiting for its first eligible next bar."""

    signal: Signal
    volume: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None = None

    def __post_init__(self) -> None:
        if self.signal.action is Action.HOLD:
            raise ValueError("HOLD signal cannot become a pending order")
        if not self.volume.is_finite() or self.volume <= 0:
            raise ValueError("pending order volume must be finite and greater than zero")
        prices = (
            (self.stop_loss,) if self.take_profit is None else (self.stop_loss, self.take_profit)
        )
        if any(not price.is_finite() or price <= 0 for price in prices):
            raise ValueError("pending protective prices must be finite and greater than zero")


@dataclass(frozen=True, slots=True)
class BacktestStep:
    """Audit result for one observable candle event."""

    event: CandleEvent
    opening_fills: tuple[SimulatedFill, ...]
    protective_exits: tuple[IntrabarExit, ...]
    expired_signal_ids: tuple[UUID, ...]
    portfolio: PortfolioState


@dataclass(frozen=True, slots=True)
class BacktestEngine:
    """Persistent event-driven backtest state with no live execution capability."""

    clock: SimulationClock
    portfolio: PortfolioState
    instruments: tuple[Instrument, ...]
    cost_models: tuple[tuple[str, ExecutionCostModel], ...]
    pending_orders: tuple[PendingOrder, ...] = ()

    def __post_init__(self) -> None:
        instrument_symbols = tuple(instrument.symbol for instrument in self.instruments)
        cost_symbols = tuple(symbol for symbol, _ in self.cost_models)
        if len(instrument_symbols) != len(set(instrument_symbols)):
            raise ValueError("backtest instruments must have unique symbols")
        if len(cost_symbols) != len(set(cost_symbols)):
            raise ValueError("backtest cost models must have unique symbols")
        if set(instrument_symbols) != set(cost_symbols):
            raise ValueError("every instrument requires exactly one cost model")

    @classmethod
    def create(
        cls,
        *,
        candles: tuple[Candle, ...],
        instruments: tuple[Instrument, ...],
        cost_models: tuple[tuple[str, ExecutionCostModel], ...],
        initial_cash: Decimal,
    ) -> "BacktestEngine":
        """Create a validated engine at the beginning of a historical dataset."""

        clock = SimulationClock.from_candles(candles)
        candle_symbols = {event.candle.symbol for event in clock.events}
        instrument_symbols = {instrument.symbol for instrument in instruments}
        if not candle_symbols <= instrument_symbols:
            raise ValueError("historical candles contain an unknown instrument")
        return cls(
            clock=clock,
            portfolio=PortfolioState(cash_balance=initial_cash),
            instruments=instruments,
            cost_models=cost_models,
        )

    def submit(self, order: PendingOrder) -> "BacktestEngine":
        """Queue one unique signal without mutating the current engine state."""

        signal_id = order.signal.id
        if any(item.signal.id == signal_id for item in self.pending_orders):
            raise ValueError("signal is already pending")
        if any(position.opening_signal_id == signal_id for position in self.portfolio.positions):
            raise ValueError("signal has already opened a position")
        if order.signal.symbol not in {instrument.symbol for instrument in self.instruments}:
            raise ValueError("pending signal references an unknown instrument")
        return replace(self, pending_orders=(*self.pending_orders, order))

    def _instrument(self, symbol: str) -> Instrument:
        return next(instrument for instrument in self.instruments if instrument.symbol == symbol)

    def _costs(self, symbol: str) -> ExecutionCostModel:
        return next(costs for item_symbol, costs in self.cost_models if item_symbol == symbol)

    def step(self) -> tuple[BacktestStep, "BacktestEngine"]:
        """Advance one event through next-bar entry, protective exits, and close marking."""

        event, advanced_clock = self.clock.advance()
        candle = event.candle
        portfolio = self.portfolio
        retained: list[PendingOrder] = []
        opening_fills: list[SimulatedFill] = []
        expired_ids: list[UUID] = []

        for order in self.pending_orders:
            signal = order.signal
            if (signal.symbol, signal.timeframe) != (candle.symbol, candle.timeframe):
                retained.append(order)
                continue
            if candle.open_time < signal.observed_at:
                retained.append(order)
                continue
            if candle.open_time > signal.expires_at:
                expired_ids.append(signal.id)
                continue
            costs = self._costs(signal.symbol)
            fill = simulate_next_bar_market_fill(signal=signal, next_bar=candle, costs=costs)
            portfolio = portfolio.open_position(
                fill=fill,
                volume=order.volume,
                instrument=self._instrument(signal.symbol),
                timeframe=signal.timeframe,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
            )
            opening_fills.append(fill)

        exits: list[IntrabarExit] = []
        matching_positions = tuple(
            position
            for position in portfolio.positions
            if (position.symbol, position.timeframe) == (candle.symbol, candle.timeframe)
        )
        for position in matching_positions:
            exit_result = simulate_conservative_intrabar_exit(
                position=position,
                candle=candle,
                costs=self._costs(position.symbol),
            )
            if exit_result is None:
                continue
            portfolio = portfolio.close_position(
                position_id=position.id,
                fill=exit_result.fill,
            )
            exits.append(exit_result)

        if any(
            (position.symbol, position.timeframe) == (candle.symbol, candle.timeframe)
            for position in portfolio.positions
        ):
            portfolio = portfolio.mark_to_market(
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                price=candle.close,
                observed_at=candle.close_time,
            )

        next_engine = BacktestEngine(
            clock=advanced_clock,
            portfolio=portfolio,
            instruments=self.instruments,
            cost_models=self.cost_models,
            pending_orders=tuple(retained),
        )
        result = BacktestStep(
            event=event,
            opening_fills=tuple(opening_fills),
            protective_exits=tuple(exits),
            expired_signal_ids=tuple(expired_ids),
            portfolio=portfolio,
        )
        return result, next_engine
