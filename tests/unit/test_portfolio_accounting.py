"""Tests for immutable multi-symbol backtest portfolio accounting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from quantora_trade.backtesting.execution import SimulatedFill
from quantora_trade.backtesting.portfolio import PortfolioState
from quantora_trade.domain.enums import Action, AssetClass
from quantora_trade.domain.models import Instrument

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)


def instrument(symbol: str, asset_class: AssetClass, tick_size: str, tick_value: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        asset_class=asset_class,
        quote_currency="USD",
        digits=5,
        point=Decimal(tick_size),
        pip_size=Decimal(tick_size),
        tick_size=Decimal(tick_size),
        tick_value=Decimal(tick_value),
        contract_size=Decimal("100000"),
        spread_points=2,
        session_timezone="UTC",
        session_profile="24x5",
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
    )


def fill(
    *,
    symbol: str,
    side: Action,
    price: str,
    commission: str,
    at: datetime = NOW,
    reference_price: str | None = None,
) -> SimulatedFill:
    return SimulatedFill(
        id=uuid4(),
        signal_id=uuid4(),
        symbol=symbol,
        side=side,
        executed_at=at,
        reference_price=Decimal(reference_price or price),
        fill_price=Decimal(price),
        spread_price=Decimal("0"),
        slippage_price=Decimal("0"),
        commission=Decimal(commission),
        cost_scenario="base",
    )


def test_multi_symbol_marks_are_isolated_and_equity_is_aggregated() -> None:
    initial = PortfolioState(cash_balance=Decimal("1000"))
    with_xau = initial.open_position(
        fill=fill(symbol="XAUUSD", side=Action.BUY, price="100", commission="2"),
        volume=Decimal("1"),
        instrument=instrument("XAUUSD", AssetClass.METAL, "0.01", "1"),
    )
    portfolio = with_xau.open_position(
        fill=fill(symbol="EURUSD", side=Action.SELL, price="1.1000", commission="1"),
        volume=Decimal("0.10"),
        instrument=instrument("EURUSD", AssetClass.FOREX, "0.0001", "10"),
    )

    marked_xau = portfolio.mark_to_market(
        symbol="XAUUSD", price=Decimal("101"), observed_at=NOW + timedelta(minutes=15)
    )
    marked = marked_xau.mark_to_market(
        symbol="EURUSD", price=Decimal("1.0990"), observed_at=NOW + timedelta(minutes=15)
    )

    assert initial.cash_balance == Decimal("1000")
    assert marked.cash_balance == Decimal("997")
    assert marked.unrealized_pnl == Decimal("110")
    assert marked.equity == Decimal("1107")
    assert with_xau.positions[0].mark_price == Decimal("100")


def test_close_position_realizes_tick_pnl_and_both_commissions() -> None:
    portfolio = PortfolioState(cash_balance=Decimal("1000")).open_position(
        fill=fill(symbol="XAUUSD", side=Action.BUY, price="100", commission="2"),
        volume=Decimal("1"),
        instrument=instrument("XAUUSD", AssetClass.METAL, "0.01", "1"),
    )
    position = portfolio.positions[0]

    closed = portfolio.close_position(
        position_id=position.id,
        fill=fill(
            symbol="XAUUSD",
            side=Action.SELL,
            price="101",
            commission="2",
            at=NOW + timedelta(minutes=15),
        ),
    )

    assert closed.positions == ()
    assert closed.cash_balance == Decimal("1096")
    assert closed.realized_pnl == Decimal("96")
    assert closed.equity == Decimal("1096")
    assert closed.closed_trades[0].gross_pnl == Decimal("100")
    assert closed.closed_trades[0].net_pnl == Decimal("96")


def test_close_position_separates_reference_pnl_execution_cost_and_commission() -> None:
    portfolio = PortfolioState(cash_balance=Decimal("1000")).open_position(
        fill=fill(
            symbol="XAUUSD",
            side=Action.BUY,
            reference_price="100",
            price="100.02",
            commission="2",
        ),
        volume=Decimal("1"),
        instrument=instrument("XAUUSD", AssetClass.METAL, "0.01", "1"),
    )

    closed = portfolio.close_position(
        position_id=portfolio.positions[0].id,
        fill=fill(
            symbol="XAUUSD",
            side=Action.SELL,
            reference_price="101",
            price="100.98",
            commission="2",
            at=NOW + timedelta(minutes=15),
        ),
    )
    result = closed.closed_trades[0]

    assert result.gross_pnl == Decimal("100")
    assert result.execution_cost == Decimal("4")
    assert result.net_pnl == Decimal("92")
    assert closed.cash_balance == Decimal("1092")


def test_margin_is_reserved_released_and_swap_is_reconciled() -> None:
    portfolio = PortfolioState(cash_balance=Decimal("1000")).open_position(
        fill=fill(symbol="XAUUSD", side=Action.BUY, price="100", commission="2"),
        volume=Decimal("1"),
        instrument=instrument("XAUUSD", AssetClass.METAL, "0.01", "1"),
        margin_required=Decimal("100"),
    )

    assert portfolio.margin_used == Decimal("100")
    assert portfolio.free_margin == Decimal("898")
    closed = portfolio.close_position(
        position_id=portfolio.positions[0].id,
        fill=fill(
            symbol="XAUUSD",
            side=Action.SELL,
            price="101",
            commission="2",
            at=NOW + timedelta(days=1),
        ),
        swap_cost=Decimal("3"),
    )

    assert closed.margin_used == 0
    assert closed.free_margin == Decimal("1093")
    assert closed.closed_trades[0].swap_cost == Decimal("3")
    assert closed.closed_trades[0].net_pnl == Decimal("93")


def test_portfolio_rejects_invalid_volume_duplicate_fill_and_close_direction() -> None:
    entry = fill(symbol="EURUSD", side=Action.BUY, price="1.1000", commission="1")
    spec = instrument("EURUSD", AssetClass.FOREX, "0.0001", "10")
    initial = PortfolioState(cash_balance=Decimal("1000"))

    with pytest.raises(ValueError, match="aligned"):
        initial.open_position(fill=entry, volume=Decimal("0.015"), instrument=spec)

    portfolio = initial.open_position(fill=entry, volume=Decimal("0.10"), instrument=spec)
    with pytest.raises(ValueError, match="already"):
        portfolio.open_position(fill=entry, volume=Decimal("0.10"), instrument=spec)
    with pytest.raises(ValueError, match="oppose"):
        portfolio.close_position(
            position_id=portfolio.positions[0].id,
            fill=fill(
                symbol="EURUSD",
                side=Action.BUY,
                price="1.1010",
                commission="1",
                at=NOW + timedelta(minutes=15),
            ),
        )


def test_mark_rejects_unknown_symbol_and_backward_time() -> None:
    portfolio = PortfolioState(cash_balance=Decimal("1000")).open_position(
        fill=fill(symbol="EURUSD", side=Action.BUY, price="1.1000", commission="1"),
        volume=Decimal("0.10"),
        instrument=instrument("EURUSD", AssetClass.FOREX, "0.0001", "10"),
    )

    with pytest.raises(ValueError, match="no open position"):
        portfolio.mark_to_market(symbol="XAUUSD", price=Decimal("100"), observed_at=NOW)
    with pytest.raises(ValueError, match="backward"):
        portfolio.mark_to_market(
            symbol="EURUSD", price=Decimal("1.1010"), observed_at=NOW - timedelta(seconds=1)
        )
