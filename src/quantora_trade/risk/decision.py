"""Deterministic Signal-to-Decision policy boundary.

This module can preserve a directional signal or downgrade it to ``HOLD``.  It
cannot create a direction, calculate risk, or submit an order.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from quantora_trade.domain.enums import Action
from quantora_trade.domain.models import Decision, Signal

SIGNAL_HOLD = "SIGNAL_HOLD"
SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
BELOW_DECISION_CONFIDENCE = "BELOW_DECISION_CONFIDENCE"


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class DecisionPolicy:
    """Immutable, versioned configuration accepted from a validated boundary."""

    version: str
    minimum_confidence: Decimal

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("decision policy version must not be empty")
        if not isinstance(self.minimum_confidence, Decimal):
            raise TypeError("minimum_confidence must be a Decimal")
        if not self.minimum_confidence.is_finite():
            raise ValueError("minimum_confidence must be finite")
        if not Decimal("0") <= self.minimum_confidence <= Decimal("1"):
            raise ValueError("minimum_confidence must be between zero and one")


class DecisionEngine:
    """Apply a policy without adding or reversing directional intent."""

    def __init__(self, policy: DecisionPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> DecisionPolicy:
        """Return the immutable policy used by this engine."""

        return self._policy

    def decide(self, *, signal: Signal, evaluated_at: datetime) -> Decision:
        """Preserve an eligible signal or deterministically downgrade it to HOLD."""

        _require_utc(evaluated_at, "evaluated_at")

        action = signal.action
        decision_reasons: tuple[str, ...] = ()
        if signal.action is Action.HOLD:
            action = Action.HOLD
            decision_reasons = (SIGNAL_HOLD,)
        elif evaluated_at >= signal.expires_at:
            action = Action.HOLD
            decision_reasons = (SIGNAL_EXPIRED,)
        elif signal.confidence < self._policy.minimum_confidence:
            action = Action.HOLD
            decision_reasons = (BELOW_DECISION_CONFIDENCE,)

        # Signal evidence is never replaced: policy explanations are appended in
        # canonical order so the entire lineage remains stable and auditable.
        reason_codes = signal.reason_codes + tuple(sorted(decision_reasons))
        identity = json.dumps(
            {
                "action": action.value,
                "confidence": str(signal.confidence),
                "expires_at": signal.expires_at.isoformat(),
                "policy_version": self._policy.version,
                "reason_codes": reason_codes,
                "signal_id": str(signal.id),
                "symbol": signal.symbol,
                "timeframe": signal.timeframe,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        decision = Decision(
            id=uuid5(NAMESPACE_URL, identity),
            signal_id=signal.id,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            action=action,
            confidence=signal.confidence,
            policy_version=self._policy.version,
            reason_codes=reason_codes,
            expires_at=signal.expires_at,
        )
        if decision.action not in {signal.action, Action.HOLD}:
            raise AssertionError("decision policy cannot reverse signal direction")
        return decision
