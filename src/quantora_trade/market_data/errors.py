"""Market-data error taxonomy."""


class MarketDataError(Exception):
    """Base exception for market-data operations."""


class MT5UnavailableError(MarketDataError):
    """Raised when the optional MetaTrader 5 runtime is unavailable."""


class MT5ConnectionError(MarketDataError):
    """Raised when MT5 initialization or an MT5 request fails."""


class SymbolNotFoundError(MarketDataError):
    """Raised when a broker symbol cannot be resolved."""


class UnsupportedTimeframeError(MarketDataError):
    """Raised when a timeframe is not supported by the adapter."""


class DataQualityError(MarketDataError):
    """Raised when normalized market data is unsafe for analysis."""
