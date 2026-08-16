"""Deterministic Decimal performance metrics for closed backtest trades."""

from dataclasses import dataclass
from decimal import Decimal

from quantora_trade.backtesting.journal import TradeJournal


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Core return, cost, trade-quality, streak, and drawdown measures."""

    initial_equity: Decimal
    ending_equity: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    total_costs: Decimal
    total_return: Decimal
    trade_count: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    payoff_ratio: Decimal | None
    profit_factor: Decimal | None
    expectancy: Decimal | None
    max_consecutive_wins: int
    max_consecutive_losses: int
    max_drawdown: Decimal
    max_drawdown_rate: Decimal


def _longest_streak(results: tuple[bool, ...]) -> int:
    longest = 0
    current = 0
    for result in results:
        current = current + 1 if result else 0
        longest = max(longest, current)
    return longest


def calculate_performance_metrics(
    *, journal: TradeJournal, initial_equity: Decimal
) -> PerformanceMetrics:
    """Calculate closed-trade metrics with a high-water-mark drawdown curve."""

    if not initial_equity.is_finite() or initial_equity <= 0:
        raise ValueError("initial equity must be finite and greater than zero")

    net_results = tuple(trade.net_pnl for trade in journal.trades)
    wins = tuple(result for result in net_results if result > 0)
    losses = tuple(result for result in net_results if result < 0)
    gross_pnl = sum((trade.gross_pnl for trade in journal.trades), Decimal("0"))
    net_pnl = sum(net_results, Decimal("0"))
    total_costs = journal.total_costs
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = -sum(losses, Decimal("0"))
    count = len(net_results)

    equity = initial_equity
    peak = initial_equity
    max_drawdown = Decimal("0")
    max_drawdown_rate = Decimal("0")
    for result in net_results:
        equity += result
        peak = max(peak, equity)
        drawdown = peak - equity
        drawdown_rate = drawdown / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown
        if drawdown_rate > max_drawdown_rate:
            max_drawdown_rate = drawdown_rate

    average_win = gross_profit / len(wins) if wins else None
    average_loss = gross_loss / len(losses) if losses else None
    return PerformanceMetrics(
        initial_equity=initial_equity,
        ending_equity=initial_equity + net_pnl,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        total_costs=total_costs,
        total_return=net_pnl / initial_equity,
        trade_count=count,
        winning_trades=len(wins),
        losing_trades=len(losses),
        breakeven_trades=count - len(wins) - len(losses),
        win_rate=Decimal(len(wins)) / count if count else None,
        average_win=average_win,
        average_loss=average_loss,
        payoff_ratio=(average_win / average_loss if average_win and average_loss else None),
        profit_factor=(gross_profit / gross_loss if gross_loss else None),
        expectancy=net_pnl / count if count else None,
        max_consecutive_wins=_longest_streak(tuple(result > 0 for result in net_results)),
        max_consecutive_losses=_longest_streak(tuple(result < 0 for result in net_results)),
        max_drawdown=max_drawdown,
        max_drawdown_rate=max_drawdown_rate,
    )
