"""Immutable, deterministic trade journal for backtest audit and replay."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from quantora_trade.backtesting.portfolio import ClosedTrade, PortfolioState
from quantora_trade.domain.enums import Action


@dataclass(frozen=True, slots=True)
class TradeJournal:
    """Chronological closed trades with stable filtering and serialization."""

    trades: tuple[ClosedTrade, ...] = ()

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(self.trades, key=lambda trade: (trade.closed_at, str(trade.position_id)))
        )
        if self.trades != ordered:
            raise ValueError("trade journal must be in deterministic close order")
        for field_name, identities in (
            ("position", tuple(trade.position_id for trade in self.trades)),
            ("opening fill", tuple(trade.opening_fill_id for trade in self.trades)),
            ("closing fill", tuple(trade.closing_fill_id for trade in self.trades)),
        ):
            if len(identities) != len(set(identities)):
                raise ValueError(f"trade journal contains duplicate {field_name} IDs")

    @classmethod
    def from_portfolio(cls, portfolio: PortfolioState) -> "TradeJournal":
        """Create a stable journal from a portfolio snapshot."""

        return cls(
            trades=tuple(
                sorted(
                    portfolio.closed_trades,
                    key=lambda trade: (trade.closed_at, str(trade.position_id)),
                )
            )
        )

    @property
    def total_net_pnl(self) -> Decimal:
        return sum((trade.net_pnl for trade in self.trades), Decimal("0"))

    @property
    def total_costs(self) -> Decimal:
        return sum(
            (
                trade.execution_cost + trade.entry_commission + trade.exit_commission
                for trade in self.trades
            ),
            Decimal("0"),
        )

    def filter(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        side: Action | None = None,
    ) -> "TradeJournal":
        """Return a deterministic journal segment without mutating the source."""

        if symbol is not None and symbol != symbol.strip().upper():
            raise ValueError("trade journal symbol filter must be canonical uppercase")
        if timeframe is not None and timeframe not in {"M5", "M15", "H1"}:
            raise ValueError("trade journal timeframe filter is not supported")
        if side is not None and (not isinstance(side, Action) or side is Action.HOLD):
            raise ValueError("trade journal side filter must be BUY or SELL")
        return TradeJournal(
            trades=tuple(
                trade
                for trade in self.trades
                if (symbol is None or trade.symbol == symbol)
                and (timeframe is None or trade.timeframe == timeframe)
                and (side is None or trade.side is side)
            )
        )

    def reconcile(self, *, initial_cash: Decimal, final_portfolio: PortfolioState) -> None:
        """Fail when a fully closed portfolio does not reconcile with journal P&L."""

        if not initial_cash.is_finite() or initial_cash < 0:
            raise ValueError("initial cash must be finite and non-negative")
        if final_portfolio.positions:
            raise ValueError("cannot reconcile a journal while positions remain open")
        if self.trades != TradeJournal.from_portfolio(final_portfolio).trades:
            raise ValueError("trade journal does not match final portfolio trades")
        if initial_cash + self.total_net_pnl != final_portfolio.cash_balance:
            raise ValueError("trade journal does not reconcile with final cash balance")

    def to_records(self) -> tuple[dict[str, Any], ...]:
        """Produce JSON-safe records while preserving exact Decimal values as strings."""

        return tuple(
            {
                "position_id": str(trade.position_id),
                "opening_fill_id": str(trade.opening_fill_id),
                "closing_fill_id": str(trade.closing_fill_id),
                "opening_signal_id": str(trade.opening_signal_id),
                "symbol": trade.symbol,
                "timeframe": trade.timeframe,
                "side": trade.side.value,
                "volume": str(trade.volume),
                "entry_reference_price": str(trade.entry_reference_price),
                "exit_reference_price": str(trade.exit_reference_price),
                "entry_price": str(trade.entry_price),
                "exit_price": str(trade.exit_price),
                "opened_at": trade.opened_at.isoformat(),
                "closed_at": trade.closed_at.isoformat(),
                "holding_seconds": str(
                    Decimal((trade.closed_at - trade.opened_at).days * 86_400)
                    + Decimal((trade.closed_at - trade.opened_at).seconds)
                    + Decimal((trade.closed_at - trade.opened_at).microseconds) / Decimal("1000000")
                ),
                "gross_pnl": str(trade.gross_pnl),
                "execution_cost": str(trade.execution_cost),
                "entry_commission": str(trade.entry_commission),
                "exit_commission": str(trade.exit_commission),
                "net_pnl": str(trade.net_pnl),
            }
            for trade in self.trades
        )
