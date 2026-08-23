"""Pure, deterministic portfolio exposure calculations.

The module deliberately has no broker or execution dependency.  All quantities are
``Decimal`` and invalid or incomplete inputs raise ``ValueError`` (fail closed).
"""

from dataclasses import dataclass
from decimal import Decimal

from quantora_trade.domain.enums import Action, AssetClass

type Aggregate = tuple[tuple[str, Decimal], ...]


def _positive(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")


def decompose_symbol(symbol: str, asset_class: AssetClass) -> tuple[str, str]:
    """Return canonical base/quote legs for supported Forex and metal symbols."""
    canonical = symbol.strip().upper()
    if (
        canonical != symbol
        or len(canonical) != 6
        or not canonical.isascii()
        or not canonical.isalpha()
    ):
        raise ValueError("symbol must be six uppercase ASCII letters")
    base, quote = canonical[:3], canonical[3:]
    if asset_class is AssetClass.FOREX:
        if base == quote:
            raise ValueError("Forex base and quote currencies must differ")
    elif asset_class is AssetClass.METAL:
        if base != "XAU" or quote != "USD":
            raise ValueError("only XAUUSD metal decomposition is supported")
    else:
        raise ValueError("unsupported asset class")
    return base, quote


@dataclass(frozen=True, slots=True)
class ExposureRecord:
    """Validated common shape for an existing or proposed exposure."""

    symbol: str
    strategy: str
    asset_class: AssetClass
    action: Action
    volume: Decimal
    contract_size: Decimal
    price: Decimal
    monetary_risk: Decimal = Decimal("0")
    stop_loss: Decimal | None = None

    def __post_init__(self) -> None:
        decompose_symbol(self.symbol, self.asset_class)
        if not self.strategy.strip() or self.strategy != self.strategy.strip():
            raise ValueError("strategy must be a non-empty trimmed value")
        if self.action not in (Action.BUY, Action.SELL):
            raise ValueError("exposure action must be BUY or SELL")
        _positive(self.volume, "volume")
        _positive(self.contract_size, "contract_size")
        _positive(self.price, "price")
        if not self.monetary_risk.is_finite() or self.monetary_risk < 0:
            raise ValueError("monetary_risk must be finite and non-negative")
        if self.stop_loss is not None and (not self.stop_loss.is_finite() or self.stop_loss <= 0):
            raise ValueError("stop_loss must be finite and greater than zero")

    def _validate_protected_risk(self) -> None:
        _positive(self.monetary_risk, "monetary_risk")
        if self.stop_loss is None:
            raise ValueError("reconciled exposure requires stop_loss")
        if self.action is Action.BUY and self.stop_loss >= self.price:
            raise ValueError("BUY exposure stop_loss must be below price")
        if self.action is Action.SELL and self.stop_loss <= self.price:
            raise ValueError("SELL exposure stop_loss must be above price")


@dataclass(frozen=True, slots=True)
class OpenExposure(ExposureRecord):
    """An immutable reconciled open position."""

    def __post_init__(self) -> None:
        super(OpenExposure, self).__post_init__()
        self._validate_protected_risk()


@dataclass(frozen=True, slots=True)
class PendingExposure(ExposureRecord):
    """An immutable reconciled pending order exposure."""

    def __post_init__(self) -> None:
        super(PendingExposure, self).__post_init__()
        self._validate_protected_risk()


@dataclass(frozen=True, slots=True)
class CandidateExposure(ExposureRecord):
    """A what-if exposure; never represents permission to execute."""


def signed_notional(record: ExposureRecord) -> Decimal:
    """Quote-currency notional, positive for BUY and negative for SELL."""
    direction = Decimal(1) if record.action is Action.BUY else Decimal(-1)
    return direction * record.volume * record.contract_size * record.price


def currency_legs(record: ExposureRecord) -> Aggregate:
    """Return signed base units and quote-currency cash leg."""
    base, quote = decompose_symbol(record.symbol, record.asset_class)
    direction = Decimal(1) if record.action is Action.BUY else Decimal(-1)
    base_units = direction * record.volume * record.contract_size
    return ((base, base_units), (quote, -signed_notional(record)))


def with_candidate(
    existing: tuple[OpenExposure | PendingExposure, ...], candidate: CandidateExposure
) -> tuple[ExposureRecord, ...]:
    """Create an immutable what-if portfolio without mutating reconciled state."""
    return (*existing, candidate)


def _aggregate(pairs: tuple[tuple[str, Decimal], ...]) -> Aggregate:
    totals: dict[str, Decimal] = {}
    for key, value in pairs:
        if not key or not value.is_finite():
            raise ValueError("aggregate input is malformed")
        totals[key] = totals.get(key, Decimal(0)) + value
    return tuple(sorted(totals.items()))


def net_notional(records: tuple[ExposureRecord, ...]) -> Decimal:
    return sum((signed_notional(record) for record in records), start=Decimal(0))


def gross_notional(
    records: tuple[ExposureRecord, ...],
    *,
    account_currency: str = "USD",
    fx_rates: Aggregate = (),
) -> Decimal:
    """Gross account-currency notional; never adds unlike currencies directly."""
    rates = dict(fx_rates)
    total = Decimal(0)
    for record in records:
        _, quote = decompose_symbol(record.symbol, record.asset_class)
        rate = Decimal(1) if quote == account_currency else rates.get(quote)
        if rate is None:
            raise ValueError(f"missing {quote}->{account_currency} FX conversion rate")
        _positive(rate, f"fx_rate[{quote}]")
        total += abs(signed_notional(record)) * rate
    return total


def aggregate_by_symbol(records: tuple[ExposureRecord, ...]) -> Aggregate:
    return _aggregate(tuple((record.symbol, abs(signed_notional(record))) for record in records))


def aggregate_by_strategy(records: tuple[ExposureRecord, ...]) -> Aggregate:
    return _aggregate(tuple((record.strategy, abs(signed_notional(record))) for record in records))


def aggregate_by_asset(records: tuple[ExposureRecord, ...]) -> Aggregate:
    return _aggregate(
        tuple((record.asset_class.value, abs(signed_notional(record))) for record in records)
    )


def aggregate_by_currency(records: tuple[ExposureRecord, ...]) -> Aggregate:
    """Directional native-unit currency exposure for reporting/hedging."""
    return _aggregate(tuple(leg for record in records for leg in currency_legs(record)))


def _converted_gross_groups(
    records: tuple[ExposureRecord, ...], limits: "ExposureLimits"
) -> tuple[Aggregate, Aggregate, Aggregate]:
    rates = dict(limits.fx_rates)
    pairs: list[tuple[str, str, Decimal]] = []
    for record in records:
        _, quote = decompose_symbol(record.symbol, record.asset_class)
        rate = Decimal(1) if quote == limits.account_currency else rates.get(quote)
        if rate is None:
            raise ValueError(f"missing {quote}->{limits.account_currency} FX conversion rate")
        _positive(rate, f"fx_rate[{quote}]")
        pairs.append(
            (record.strategy, record.asset_class.value, abs(signed_notional(record)) * rate)
        )
    return (
        _aggregate(
            tuple(
                (record.symbol, amount)
                for record, (_, _, amount) in zip(records, pairs, strict=True)
            )
        ),
        _aggregate(tuple((strategy, amount) for strategy, _, amount in pairs)),
        _aggregate(tuple((asset, amount) for _, asset, amount in pairs)),
    )


@dataclass(frozen=True, slots=True)
class ExposureLimits:
    """Absolute hard limits expressed in the native units of each aggregate."""

    gross_notional: Decimal
    per_symbol: Aggregate = ()
    per_strategy: Aggregate = ()
    per_asset: Aggregate = ()
    per_currency: Aggregate = ()
    account_currency: str = "USD"
    fx_rates: Aggregate = ()

    def __post_init__(self) -> None:
        _positive(self.gross_notional, "gross_notional")
        if len(self.account_currency) != 3 or not self.account_currency.isupper():
            raise ValueError("account_currency must be three uppercase letters")
        for currency, rate in self.fx_rates:
            if len(currency) != 3 or not currency.isupper():
                raise ValueError("FX rate currency must be three uppercase letters")
            _positive(rate, f"fx_rate[{currency}]")
        for group in (self.per_symbol, self.per_strategy, self.per_asset, self.per_currency):
            keys: set[str] = set()
            for key, value in group:
                if not key or key in keys:
                    raise ValueError("limit keys must be non-empty and unique")
                _positive(value, f"limit[{key}]")
                keys.add(key)


def limit_breaches(records: tuple[ExposureRecord, ...], limits: ExposureLimits) -> tuple[str, ...]:
    """Return stable machine-readable breaches; absence means within limits."""
    breaches: list[str] = []
    if (
        gross_notional(
            records,
            account_currency=limits.account_currency,
            fx_rates=limits.fx_rates,
        )
        > limits.gross_notional
    ):
        breaches.append("GROSS_NOTIONAL_LIMIT")
    symbols, strategies, assets = _converted_gross_groups(records, limits)
    gross_currency = _aggregate(
        tuple(
            (currency, abs(amount))
            for record in records
            for currency, amount in currency_legs(record)
        )
    )
    groups = (
        ("SYMBOL", symbols, limits.per_symbol),
        ("STRATEGY", strategies, limits.per_strategy),
        ("ASSET", assets, limits.per_asset),
        ("CURRENCY", gross_currency, limits.per_currency),
    )
    for label, actual, configured in groups:
        actual_map = dict(actual)
        for key, maximum in configured:
            if abs(actual_map.get(key, Decimal(0))) > maximum:
                breaches.append(f"{label}_LIMIT:{key}")
    return tuple(breaches)
