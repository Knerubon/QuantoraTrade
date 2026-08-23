"""Deterministic average-cost PAPER accounting; no broker or market-data access."""

from decimal import Decimal

from quantora_trade.accounting.models import AccountingFill, AccountSnapshot, PositionSnapshot


def apply_fill(
    position: PositionSnapshot | None, fill: AccountingFill
) -> tuple[PositionSnapshot, Decimal]:
    """Apply one fill and return the new position plus gross realized P&L.

    Fees are tracked separately and never hidden inside realized market P&L.  A fill
    may add, reduce, close, or reverse a position.  Replaying is prevented by the
    repository's unique ``(order_id, sequence)`` evidence key.
    """

    signed_fill = fill.quantity if fill.side == "buy" else -fill.quantity
    if position is None:
        return _position_from_fill(fill, signed_fill), Decimal("0")
    if (
        position.instrument_id != fill.instrument_id
        or position.broker_id != fill.broker_id
        or position.specification_hash != fill.specification_hash
        or position.symbol != fill.symbol
        or position.quote_currency != fill.quote_currency
        or position.contract_multiplier != fill.contract_multiplier
    ):
        raise ValueError("fill specification does not match the persisted position")

    old_qty = position.net_quantity
    same_direction = old_qty == 0 or (old_qty > 0) == (signed_fill > 0)
    if same_direction:
        new_qty = old_qty + signed_fill
        average = (abs(old_qty) * position.average_price + abs(signed_fill) * fill.price) / abs(
            new_qty
        )
        realized = Decimal("0")
    else:
        closed_qty = min(abs(old_qty), abs(signed_fill))
        direction = Decimal("1") if old_qty > 0 else Decimal("-1")
        realized = (
            (fill.price - position.average_price)
            * closed_qty
            * fill.contract_multiplier
            * direction
        )
        new_qty = old_qty + signed_fill
        average = (
            Decimal("0")
            if new_qty == 0
            else (position.average_price if (new_qty > 0) == (old_qty > 0) else fill.price)
        )

    mark = Decimal("0") if new_qty == 0 else fill.price
    updated = PositionSnapshot(
        instrument_id=position.instrument_id,
        broker_id=position.broker_id,
        specification_hash=position.specification_hash,
        symbol=position.symbol,
        quote_currency=position.quote_currency,
        net_quantity=new_qty,
        average_price=average,
        mark_price=mark,
        contract_multiplier=position.contract_multiplier,
        realized_pnl=position.realized_pnl + realized,
        unrealized_pnl=Decimal("0"),
        fees=position.fees + fill.commission,
    )
    return updated, realized


def mark_position(position: PositionSnapshot, price: Decimal) -> PositionSnapshot:
    if price <= 0:
        raise ValueError("mark price must be positive")
    unrealized = (
        (price - position.average_price) * position.net_quantity * position.contract_multiplier
    )
    return position.model_copy(update={"mark_price": price, "unrealized_pnl": unrealized})


def update_account(
    account: AccountSnapshot,
    positions: tuple[PositionSnapshot, ...],
    *,
    realized_delta: Decimal,
    fee_delta: Decimal,
) -> AccountSnapshot:
    mismatched = tuple(item.symbol for item in positions if item.quote_currency != account.currency)
    if mismatched:
        raise ValueError(
            "positions must be valued in the account currency; FX conversion unavailable"
        )
    realized = account.realized_pnl + realized_delta
    fees = account.fees + fee_delta
    cash = account.initial_balance + realized - fees
    unrealized = sum((item.unrealized_pnl for item in positions), Decimal("0"))
    equity = cash + unrealized
    peak = max(account.equity_peak, equity)
    drawdown = peak - equity
    return AccountSnapshot(
        currency=account.currency,
        initial_balance=account.initial_balance,
        cash_balance=cash,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        fees=fees,
        equity=equity,
        equity_peak=peak,
        drawdown=drawdown,
        drawdown_pct=Decimal("0") if peak <= 0 else drawdown / peak,
    )


def _position_from_fill(fill: AccountingFill, signed_quantity: Decimal) -> PositionSnapshot:
    return PositionSnapshot(
        instrument_id=fill.instrument_id,
        broker_id=fill.broker_id,
        specification_hash=fill.specification_hash,
        symbol=fill.symbol,
        quote_currency=fill.quote_currency,
        net_quantity=signed_quantity,
        average_price=fill.price,
        mark_price=fill.price,
        contract_multiplier=fill.contract_multiplier,
        fees=fill.commission,
    )


__all__ = ["apply_fill", "mark_position", "update_account"]
