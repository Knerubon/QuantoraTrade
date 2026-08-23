"""Tests for deterministic portfolio/currency exposure primitives."""

from decimal import Decimal

import pytest

from quantora_trade.domain.enums import Action, AssetClass
from quantora_trade.risk.exposure import (
    CandidateExposure,
    ExposureLimits,
    OpenExposure,
    PendingExposure,
    aggregate_by_asset,
    aggregate_by_currency,
    aggregate_by_strategy,
    aggregate_by_symbol,
    currency_legs,
    decompose_symbol,
    gross_notional,
    limit_breaches,
    net_notional,
    with_candidate,
)


def exposure(
    cls: type[OpenExposure | PendingExposure | CandidateExposure] = OpenExposure,
    **overrides: object,
) -> OpenExposure | PendingExposure | CandidateExposure:
    values: dict[str, object] = dict(
        symbol="EURUSD",
        strategy="trend-v1",
        asset_class=AssetClass.FOREX,
        action=Action.BUY,
        volume=Decimal("1"),
        contract_size=Decimal("100000"),
        price=Decimal("1.10"),
        monetary_risk=Decimal("100"),
    )
    values.update(overrides)
    if cls is not CandidateExposure:
        values.setdefault(
            "stop_loss",
            Decimal("1.09") if values["action"] is Action.BUY else Decimal("1.11"),
        )
    return cls(**values)  # type: ignore[arg-type]


def test_decomposes_forex_and_supported_metal() -> None:
    assert decompose_symbol("EURUSD", AssetClass.FOREX) == ("EUR", "USD")
    assert decompose_symbol("XAUUSD", AssetClass.METAL) == ("XAU", "USD")


@pytest.mark.parametrize("symbol", ["eurusd", "EUR/USD", "USDUSD", "EURUS1"])
def test_malformed_forex_symbol_fails_closed(symbol: str) -> None:
    with pytest.raises(ValueError):
        exposure(symbol=symbol)


def test_unsupported_metal_fails_closed() -> None:
    with pytest.raises(ValueError):
        exposure(symbol="XAGUSD", asset_class=AssetClass.METAL)


@pytest.mark.parametrize(
    ("override", "value"),
    [("volume", "0"), ("price", "NaN"), ("contract_size", "Infinity")],
)
def test_malformed_numeric_data_fails_closed(override: str, value: str) -> None:
    with pytest.raises(ValueError):
        exposure(**{override: Decimal(value)})


def test_hold_and_blank_strategy_fail_closed() -> None:
    with pytest.raises(ValueError):
        exposure(action=Action.HOLD)
    with pytest.raises(ValueError):
        exposure(strategy=" ")


def test_signed_currency_legs_buy_and_sell() -> None:
    buy = exposure()
    sell = exposure(action=Action.SELL, volume=Decimal("0.5"))
    assert currency_legs(buy) == (("EUR", Decimal("100000")), ("USD", Decimal("-110000.00")))
    assert currency_legs(sell) == (("EUR", Decimal("-50000.0")), ("USD", Decimal("55000.000")))


def test_gross_net_and_all_aggregates() -> None:
    records = (
        exposure(),
        exposure(PendingExposure, action=Action.SELL, volume=Decimal("0.5")),
        exposure(
            CandidateExposure,
            symbol="XAUUSD",
            strategy="mean-v1",
            asset_class=AssetClass.METAL,
            volume=Decimal("2"),
            contract_size=Decimal("100"),
            price=Decimal("2400"),
        ),
    )
    assert net_notional(records) == Decimal("535000")
    assert gross_notional(records) == Decimal("645000")
    assert aggregate_by_symbol(records) == (
        ("EURUSD", Decimal("165000")),
        ("XAUUSD", Decimal("480000")),
    )
    assert aggregate_by_strategy(records) == (
        ("mean-v1", Decimal("480000")),
        ("trend-v1", Decimal("165000")),
    )
    assert aggregate_by_asset(records) == (
        ("forex", Decimal("165000")),
        ("metal", Decimal("480000")),
    )
    assert aggregate_by_currency(records) == (
        ("EUR", Decimal("50000")),
        ("USD", Decimal("-535000")),
        ("XAU", Decimal("200")),
    )


def test_what_if_candidate_does_not_mutate_existing() -> None:
    existing = (exposure(),)
    candidate = exposure(CandidateExposure, action=Action.SELL)
    result = with_candidate(existing, candidate)
    assert len(existing) == 1
    assert result == (*existing, candidate)


def test_limits_cover_gross_and_each_aggregate_dimension() -> None:
    records = (exposure(),)
    limits = ExposureLimits(
        gross_notional=Decimal("100000"),
        per_symbol=(("EURUSD", Decimal("100000")),),
        per_strategy=(("trend-v1", Decimal("100000")),),
        per_asset=(("forex", Decimal("100000")),),
        per_currency=(("EUR", Decimal("90000")), ("USD", Decimal("100000"))),
    )
    assert limit_breaches(records, limits) == (
        "GROSS_NOTIONAL_LIMIT",
        "SYMBOL_LIMIT:EURUSD",
        "STRATEGY_LIMIT:trend-v1",
        "ASSET_LIMIT:forex",
        "CURRENCY_LIMIT:EUR",
        "CURRENCY_LIMIT:USD",
    )


def test_limit_boundary_is_allowed_and_invalid_limits_fail_closed() -> None:
    record = exposure()
    limits = ExposureLimits(
        gross_notional=Decimal("110000"), per_symbol=(("EURUSD", Decimal("110000")),)
    )
    assert limit_breaches((record,), limits) == ()
    with pytest.raises(ValueError):
        ExposureLimits(
            Decimal("1"),
            per_symbol=(("EURUSD", Decimal("1")), ("EURUSD", Decimal("2"))),
        )


def test_opposite_positions_do_not_cancel_concentration() -> None:
    records = (exposure(), exposure(action=Action.SELL))
    limits = ExposureLimits(
        gross_notional=Decimal("220000"),
        per_symbol=(("EURUSD", Decimal("219999")),),
    )
    assert limit_breaches(records, limits) == ("SYMBOL_LIMIT:EURUSD",)


def test_mixed_quote_gross_notional_requires_explicit_conversion() -> None:
    records = (exposure(), exposure(symbol="EURJPY", price=Decimal("160")))
    with pytest.raises(ValueError, match="JPY->USD"):
        gross_notional(records)
    assert gross_notional(records, fx_rates=(("JPY", Decimal("0.0068")),)) > 0
