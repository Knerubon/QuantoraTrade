"""Immutable orchestration of clock, pending signals, fills, exits, and accounting."""

from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from quantora_trade.backtesting.broker import (
    BrokerFillDecision,
    BrokerSimulationModel,
    FillStatus,
    calculate_swap_cost,
    simulate_broker_fill_decision,
)
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
    fill_decisions: tuple[BrokerFillDecision, ...]
    portfolio: PortfolioState


@dataclass(frozen=True, slots=True)
class BacktestEngine:
    """Persistent event-driven backtest state with no live execution capability."""

    clock: SimulationClock
    portfolio: PortfolioState
    instruments: tuple[Instrument, ...]
    cost_models: tuple[tuple[str, ExecutionCostModel], ...]
    broker_models: tuple[tuple[str, BrokerSimulationModel], ...] = ()
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
        broker_symbols = tuple(symbol for symbol, _ in self.broker_models)
        if len(broker_symbols) != len(set(broker_symbols)):
            raise ValueError("backtest broker models must have unique symbols")
        if not set(broker_symbols) <= set(instrument_symbols):
            raise ValueError("broker model references an unknown instrument")

    @classmethod
    def create(
        cls,
        *,
        candles: tuple[Candle, ...],
        instruments: tuple[Instrument, ...],
        cost_models: tuple[tuple[str, ExecutionCostModel], ...],
        initial_cash: Decimal,
        broker_models: tuple[tuple[str, BrokerSimulationModel], ...] = (),
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
            broker_models=broker_models,
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

    def _broker(self, symbol: str) -> BrokerSimulationModel:
        return next(
            (model for item_symbol, model in self.broker_models if item_symbol == symbol),
            BrokerSimulationModel(),
        )

    def step(self) -> tuple[BacktestStep, "BacktestEngine"]:
        """Advance one event through next-bar entry, protective exits, and close marking."""

        event, advanced_clock = self.clock.advance()
        candle = event.candle
        portfolio = self.portfolio
        retained: list[PendingOrder] = []
        opening_fills: list[SimulatedFill] = []
        fill_decisions: list[BrokerFillDecision] = []
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
            decision = simulate_broker_fill_decision(
                requested_volume=order.volume,
                available_margin=max(portfolio.free_margin, Decimal("0")),
                instrument=self._instrument(signal.symbol),
                model=self._broker(signal.symbol),
                commission_per_lot=costs.commission_per_side,
            )
            fill_decisions.append(decision)
            if decision.status is FillStatus.REJECTED:
                continue
            fill = simulate_next_bar_market_fill(
                signal=signal,
                next_bar=candle,
                costs=costs,
                volume=decision.filled_volume,
            )
            portfolio = portfolio.open_position(
                fill=fill,
                volume=decision.filled_volume,
                instrument=self._instrument(signal.symbol),
                timeframe=signal.timeframe,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                margin_required=decision.margin_required,
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
                swap_cost=calculate_swap_cost(
                    side=position.side,
                    volume=position.volume,
                    opened_at=position.opened_at,
                    closed_at=exit_result.fill.executed_at,
                    model=self._broker(position.symbol),
                ),
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
            broker_models=self.broker_models,
            pending_orders=tuple(retained),
        )
        result = BacktestStep(
            event=event,
            opening_fills=tuple(opening_fills),
            protective_exits=tuple(exits),
            expired_signal_ids=tuple(expired_ids),
            fill_decisions=tuple(fill_decisions),
            portfolio=portfolio,
        )
        return result, next_engine
