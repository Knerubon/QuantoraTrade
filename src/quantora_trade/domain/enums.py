"""Stable domain enumerations."""

from enum import StrEnum


class Action(StrEnum):
    """Trading action proposed by analysis or decision logic."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class AssetClass(StrEnum):
    """Asset classes supported by the initial domain."""

    METAL = "metal"
    FOREX = "forex"


class TradingMode(StrEnum):
    """Execution mode for a run or command."""

    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class RiskRejectionCode(StrEnum):
    """Stable reason codes returned by the deterministic risk engine."""

    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    RISK_INPUT_INCOMPLETE = "RISK_INPUT_INCOMPLETE"
    STALE_DATA = "STALE_DATA"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    INVALID_STOP_LOSS = "INVALID_STOP_LOSS"
    TRADE_RISK_LIMIT = "TRADE_RISK_LIMIT"
    PORTFOLIO_RISK_LIMIT = "PORTFOLIO_RISK_LIMIT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    UNKNOWN_SYMBOL_SPEC = "UNKNOWN_SYMBOL_SPEC"
