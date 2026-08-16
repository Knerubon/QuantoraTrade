"""Deterministic construction of auditable, expiring strategy signals."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from quantora_trade.domain.enums import Action, SignalReasonCode
from quantora_trade.domain.models import Candle, Signal
from quantora_trade.market_data.timeframes import Timeframe
from quantora_trade.strategy.configuration import StrategyConfiguration


def build_signal(
    *,
    candle: Candle,
    action: Action,
    confidence: Decimal,
    strategy_version: str,
    reason_codes: tuple[SignalReasonCode, ...],
    ttl_bars: int = 1,
    evidence_observed_at: tuple[datetime, ...] = (),
) -> Signal:
    """Build a reproducible signal whose clock is anchored to a closed candle."""

    if not candle.is_closed:
        raise ValueError("signal requires a closed candle")
    if ttl_bars <= 0:
        raise ValueError("ttl_bars must be greater than zero")
    try:
        duration = Timeframe(candle.timeframe).duration
    except ValueError as error:
        raise ValueError(f"unsupported signal timeframe: {candle.timeframe}") from error
    observed_at = candle.close_time
    if any(
        timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp)
        for timestamp in evidence_observed_at
    ):
        raise ValueError("signal evidence timestamps must be timezone-aware UTC")
    if any(timestamp > observed_at for timestamp in evidence_observed_at):
        raise ValueError("signal evidence cannot be observed in the future")

    canonical_reasons = tuple(sorted({reason.value for reason in reason_codes}))
    if not canonical_reasons:
        raise ValueError("signal requires at least one reason code")
    normalized_confidence = Decimal(confidence)
    expires_at = observed_at + (duration * ttl_bars)
    identity = json.dumps(
        {
            "action": action.value,
            "confidence": str(normalized_confidence),
            "expires_at": expires_at.isoformat(),
            "observed_at": observed_at.isoformat(),
            "reason_codes": canonical_reasons,
            "strategy_version": strategy_version,
            "symbol": candle.symbol,
            "timeframe": candle.timeframe,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return Signal(
        id=uuid5(NAMESPACE_URL, identity),
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        action=action,
        confidence=normalized_confidence,
        strategy_version=strategy_version,
        reason_codes=canonical_reasons,
        observed_at=observed_at,
        expires_at=expires_at,
    )


def build_configured_signal(
    *,
    candle: Candle,
    proposed_action: Action,
    confidence: Decimal,
    configuration: StrategyConfiguration,
    reason_codes: tuple[SignalReasonCode, ...],
    evidence_observed_at: tuple[datetime, ...] = (),
) -> Signal:
    """Apply effective symbol thresholds before building an immutable signal."""

    parameters = configuration.resolve(candle.symbol)
    action = proposed_action
    effective_reasons = reason_codes
    if proposed_action is not Action.HOLD and confidence < parameters.minimum_confidence:
        action = Action.HOLD
        effective_reasons = (SignalReasonCode.BELOW_MINIMUM_CONFIDENCE,)
    return build_signal(
        candle=candle,
        action=action,
        confidence=confidence,
        strategy_version=configuration.version,
        reason_codes=effective_reasons,
        ttl_bars=parameters.signal_ttl_bars,
        evidence_observed_at=evidence_observed_at,
    )
