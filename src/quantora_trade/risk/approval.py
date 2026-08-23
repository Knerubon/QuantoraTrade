"""The sole Phase 5 constructor for risk-approved order intents.

This boundary constructs an immutable intent but has no broker or network capability.
"""

import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from quantora_trade.domain.enums import Action, TradingMode
from quantora_trade.domain.models import ApprovedOrderIntent, Decision, RiskAssessment


def build_approved_order_intent(
    *,
    decision: Decision,
    assessment: RiskAssessment,
    mode: TradingMode,
    created_at: datetime,
) -> ApprovedOrderIntent:
    """Copy an approved matching assessment into a deterministic order intent."""

    if created_at.tzinfo is None or created_at.utcoffset() != UTC.utcoffset(created_at):
        raise ValueError("created_at must be timezone-aware UTC")
    if not isinstance(mode, TradingMode):
        raise ValueError("mode must be a supported TradingMode")
    if decision.action is Action.HOLD:
        raise ValueError("HOLD decision cannot produce an order intent")
    if created_at >= decision.expires_at:
        raise ValueError("decision expired before order approval")
    if assessment.decision_id != decision.id:
        raise ValueError("risk assessment does not belong to decision")
    if not assessment.approved:
        raise ValueError("rejected risk assessment cannot produce an order intent")
    if assessment.stop_loss is None or assessment.volume <= 0:
        raise ValueError("approved assessment is missing protective sizing")
    if assessment.created_at > created_at:
        raise ValueError("risk assessment cannot come from the future")

    identity = json.dumps(
        {
            "assessment_id": str(assessment.id),
            "decision_id": str(decision.id),
            "mode": mode.value,
            "side": decision.action.value,
            "stop_loss": str(assessment.stop_loss),
            "symbol": decision.symbol,
            "take_profit": (
                str(assessment.take_profit) if assessment.take_profit is not None else None
            ),
            "volume": str(assessment.volume),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    identity_id = uuid5(NAMESPACE_URL, identity)
    return ApprovedOrderIntent(
        id=identity_id,
        risk_assessment_id=assessment.id,
        idempotency_key=f"quantora:{identity_id}",
        mode=mode,
        symbol=decision.symbol,
        side=decision.action,
        volume=assessment.volume,
        stop_loss=assessment.stop_loss,
        take_profit=assessment.take_profit,
        created_at=created_at,
    )
