"""Tests for deterministic trade journals and closed-trade performance metrics."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from quantora_trade.backtesting.journal import TradeJournal
from quantora_trade.backtesting.metrics import calculate_performance_metrics
from quantora_trade.backtesting.portfolio import ClosedTrade, PortfolioState
from quantora_trade.domain.enums import Action

START = datetime(2026, 1, 1, tzinfo=UTC)


def trade(index: int, net_pnl: str, *, symbol: str = "XAUUSD") -> ClosedTrade:
    net = Decimal(net_pnl)
    entry_commission = Decimal("1")
    exit_commission = Decimal("1")
    return ClosedTrade(
        position_id=uuid4(),
        opening_fill_id=uuid4(),
        closing_fill_id=uuid4(),
        opening_signal_id=uuid4(),
        symbol=symbol,
        timeframe="M15",
        side=Action.BUY if index % 2 == 0 else Action.SELL,
        volume=Decimal("1"),
        entry_reference_price=Decimal("100"),
        exit_reference_price=Decimal("101"),
        entry_price=Decimal("100"),
        exit_price=Decimal("101"),
        opened_at=START + timedelta(days=index),
        closed_at=START + timedelta(days=index, minutes=15),
        gross_pnl=net + entry_commission + exit_commission,
        execution_cost=Decimal("0"),
        entry_commission=entry_commission,
        exit_commission=exit_commission,
        net_pnl=net,
    )


def test_trade_journal_filters_serializes_and_reconciles() -> None:
    xau = trade(0, "10")
    eur = trade(1, "-4", symbol="EURUSD")
    portfolio = PortfolioState(
        cash_balance=Decimal("1006"),
        realized_pnl=Decimal("6"),
        closed_trades=(xau, eur),
    )

    journal = TradeJournal.from_portfolio(portfolio)

    assert journal.total_net_pnl == Decimal("6")
    assert journal.total_costs == Decimal("4")
    assert journal.filter(symbol="XAUUSD").trades == (xau,)
    assert journal.to_records()[0]["opening_signal_id"] == str(xau.opening_signal_id)
    assert journal.to_records()[0]["holding_seconds"] == "900"
    journal.reconcile(initial_cash=Decimal("1000"), final_portfolio=portfolio)


def test_trade_journal_rejects_bad_order_hold_filter_and_bad_reconciliation() -> None:
    first = trade(0, "1")
    second = trade(1, "1")
    with pytest.raises(ValueError, match="close order"):
        TradeJournal(trades=(second, first))

    journal = TradeJournal(trades=(first,))
    with pytest.raises(ValueError, match="BUY or SELL"):
        journal.filter(side=Action.HOLD)

    unmatched_portfolio = PortfolioState(cash_balance=Decimal("1002"))
    with pytest.raises(ValueError, match="does not match"):
        journal.reconcile(initial_cash=Decimal("1000"), final_portfolio=unmatched_portfolio)

    unmatched_cash = PortfolioState(
        cash_balance=Decimal("1002"), realized_pnl=Decimal("1"), closed_trades=(first,)
    )
    with pytest.raises(ValueError, match="does not reconcile"):
        journal.reconcile(initial_cash=Decimal("1000"), final_portfolio=unmatched_cash)


def test_metrics_cover_costs_trade_quality_streaks_and_drawdown() -> None:
    journal = TradeJournal(
        trades=tuple(
            trade(index, result) for index, result in enumerate(("10", "-5", "-7", "4", "0"))
        )
    )

    metrics = calculate_performance_metrics(journal=journal, initial_equity=Decimal("100"))

    assert metrics.ending_equity == Decimal("102")
    assert metrics.gross_pnl == Decimal("12")
    assert metrics.net_pnl == Decimal("2")
    assert metrics.total_costs == Decimal("10")
    assert metrics.total_return == Decimal("0.02")
    assert metrics.trade_count == 5
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 2
    assert metrics.breakeven_trades == 1
    assert metrics.win_rate == Decimal("0.4")
    assert metrics.average_win == Decimal("7")
    assert metrics.average_loss == Decimal("6")
    assert metrics.payoff_ratio == Decimal("7") / Decimal("6")
    assert metrics.profit_factor == Decimal("7") / Decimal("6")
    assert metrics.expectancy == Decimal("0.4")
    assert metrics.max_consecutive_wins == 1
    assert metrics.max_consecutive_losses == 2
    assert metrics.max_drawdown == Decimal("12")
    assert metrics.max_drawdown_rate == Decimal("12") / Decimal("110")


def test_empty_metrics_and_invalid_initial_equity_are_explicit() -> None:
    metrics = calculate_performance_metrics(journal=TradeJournal(), initial_equity=Decimal("100"))

    assert metrics.trade_count == 0
    assert metrics.win_rate is None
    assert metrics.profit_factor is None
    assert metrics.expectancy is None
    assert metrics.max_drawdown == 0

    with pytest.raises(ValueError, match="greater than zero"):
        calculate_performance_metrics(journal=TradeJournal(), initial_equity=Decimal("0"))
