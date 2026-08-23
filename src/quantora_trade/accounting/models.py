"""Immutable, currency-explicit PAPER accounting values."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AccountingModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AccountingFill(AccountingModel):
    order_id: UUID
    sequence: int = Field(gt=0)
    instrument_id: UUID
    broker_id: UUID
    specification_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str = Field(min_length=1, max_length=50)
    side: Literal["buy", "sell"]
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    commission: Decimal = Field(ge=0)
    filled_at: datetime
    quote_currency: str = Field(min_length=3, max_length=3)
    contract_multiplier: Decimal = Field(gt=0)


class PositionSnapshot(AccountingModel):
    instrument_id: UUID
    broker_id: UUID
    specification_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str
    quote_currency: str = Field(min_length=3, max_length=3)
    net_quantity: Decimal
    average_price: Decimal = Field(ge=0)
    mark_price: Decimal = Field(ge=0)
    contract_multiplier: Decimal = Field(gt=0)
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    fees: Decimal = Field(default=Decimal("0"), ge=0)

    @model_validator(mode="after")
    def flat_has_no_cost_basis(self) -> "PositionSnapshot":
        if self.net_quantity == 0 and (self.average_price != 0 or self.unrealized_pnl != 0):
            raise ValueError("flat position cannot retain cost basis or unrealized P&L")
        return self


class AccountSnapshot(AccountingModel):
    currency: str = Field(min_length=3, max_length=3)
    initial_balance: Decimal
    cash_balance: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    fees: Decimal = Field(ge=0)
    equity: Decimal
    equity_peak: Decimal
    drawdown: Decimal = Field(ge=0)
    drawdown_pct: Decimal = Field(ge=0)
