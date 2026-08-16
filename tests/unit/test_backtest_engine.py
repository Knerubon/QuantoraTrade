"""Integration-style tests for immutable event-driven backtest orchestration."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantora_trade.backtesting.broker import BrokerSimulationModel, FillReason, FillStatus
from quantora_trade.backtesting.engine import BacktestEngine, PendingOrder
from quantora_trade.backtesting.execution import ExecutionCostModel
from quantora_trade.domain.enums import Action, AssetClass, SignalReasonCode
from quantora_trade.domain.models import Candle, Instrument
from quantora_trade.strategy.signals import build_signal

START = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)


def candle(index: int, *, open_: str, high: str, low: str, close: str) -> Candle:
    open_time = START + timedelta(minutes=15 * index)
    return Candle(
        symbol="XAUUSD",
        timeframe="M15",
        open_time=open_time,
        close_time=open_time + timedelta(minutes=15),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        tick_volume=100,
        is_closed=True,
    )


def instrument() -> Instrument:
    return Instrument(
        symbol="XAUUSD",
        asset_class=AssetClass.METAL,
        quote_currency="USD",
        digits=2,
        point=Decimal("0.01"),
        pip_size=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        tick_value=Decimal("1"),
        contract_size=Decimal("100"),
        spread_points=0,
        session_timezone="UTC",
        session_profile="24x5",
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
    )


def costs() -> ExecutionCostModel:
    return ExecutionCostModel(
        point=Decimal("0.01"),
        spread_points=Decimal("0"),
        slippage_points=Decimal("0"),
        commission_per_side=Decimal("1"),
    )


def engine(
    candles: tuple[Candle, ...], broker: BrokerSimulationModel | None = None
) -> BacktestEngine:
    return BacktestEngine.create(
        candles=candles,
        instruments=(instrument(),),
        cost_models=(("XAUUSD", costs()),),
        initial_cash=Decimal("1000"),
        broker_models=() if broker is None else (("XAUUSD", broker),),
    )


def signal(source: Candle, *, ttl_bars: int = 1):
    return build_signal(
        candle=source,
        action=Action.BUY,
        confidence=Decimal("0.75"),
        strategy_version="technical-v1",
        reason_codes=(SignalReasonCode.EMA_BULLISH_ALIGNMENT,),
        ttl_bars=ttl_bars,
    )


def test_engine_executes_only_next_bar_then_applies_protective_exit() -> None:
    source = candle(0, open_="99", high="100", low="98", close="99.5")
    entry_bar = candle(1, open_="100", high="101.5", low="99.5", close="101")
    configured = engine((source, entry_bar)).submit(
        PendingOrder(
            signal=signal(source),
            volume=Decimal("1"),
            stop_loss=Decimal("99"),
            take_profit=Decimal("101"),
        )
    )

    first, after_first = configured.step()
    second, completed = after_first.step()

    assert first.opening_fills == ()
    assert len(after_first.pending_orders) == 1
    assert second.opening_fills[0].executed_at == entry_bar.open_time
    assert len(second.protective_exits) == 1
    assert second.portfolio.positions == ()
    assert second.portfolio.realized_pnl == Decimal("98")
    assert second.portfolio.cash_balance == Decimal("1098")
    assert completed.clock.is_finished
    assert completed.pending_orders == ()


def test_engine_expires_signal_when_first_eligible_bar_is_too_late() -> None:
    source = candle(0, open_="99", high="100", low="98", close="99.5")
    late = Candle(
        symbol="XAUUSD",
        timeframe="M15",
        open_time=START + timedelta(minutes=45),
        close_time=START + timedelta(minutes=60),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        tick_volume=100,
        is_closed=True,
    )
    configured = engine((source, late)).submit(
        PendingOrder(
            signal=signal(source),
            volume=Decimal("1"),
            stop_loss=Decimal("99"),
        )
    )

    _, after_first = configured.step()
    result, completed = after_first.step()

    assert result.opening_fills == ()
    assert result.expired_signal_ids == (signal(source).id,)
    assert result.portfolio.cash_balance == Decimal("1000")
    assert completed.pending_orders == ()


def test_engine_books_partial_fill_and_rejects_unaffordable_order() -> None:
    source = candle(0, open_="99", high="100", low="98", close="99.5")
    entry_bar = candle(1, open_="100", high="100.5", low="99.5", close="100")
    partial_engine = engine(
        (source, entry_bar),
        BrokerSimulationModel(margin_per_lot=Decimal("100"), liquidity_cap=Decimal("0.50")),
    ).submit(
        PendingOrder(
            signal=signal(source),
            volume=Decimal("1"),
            stop_loss=Decimal("99"),
        )
    )

    _, after_source = partial_engine.step()
    partial, _ = after_source.step()

    assert partial.fill_decisions[0].status is FillStatus.PARTIAL
    assert partial.fill_decisions[0].filled_volume == Decimal("0.50")
    assert partial.portfolio.positions[0].volume == Decimal("0.50")
    assert partial.portfolio.margin_used == Decimal("50.00")

    rejected_engine = engine(
        (source, entry_bar), BrokerSimulationModel(margin_per_lot=Decimal("100000"))
    ).submit(
        PendingOrder(
            signal=signal(source),
            volume=Decimal("1"),
            stop_loss=Decimal("99"),
        )
    )
    _, after_source = rejected_engine.step()
    rejected, _ = after_source.step()

    assert rejected.fill_decisions[0].status is FillStatus.REJECTED
    assert FillReason.INSUFFICIENT_MARGIN in rejected.fill_decisions[0].reason_codes
    assert rejected.opening_fills == ()
    assert rejected.portfolio.positions == ()
