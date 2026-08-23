"""Deterministic position sizing and fail-closed pre-trade risk gates."""

import json
from decimal import ROUND_FLOOR, Decimal
from uuid import NAMESPACE_URL, uuid5

from quantora_trade.domain.enums import Action, RiskRejectionCode
from quantora_trade.domain.models import RiskAssessment
from quantora_trade.risk.exposure import CandidateExposure, limit_breaches, with_candidate
from quantora_trade.risk.models import RiskPolicy, TradeRiskRequest


def _drawdown(peak: Decimal, equity: Decimal) -> Decimal:
    return max(Decimal("0"), (peak - equity) / peak)


def _rounded_down(raw: Decimal, step: Decimal) -> Decimal:
    return (raw / step).to_integral_value(rounding=ROUND_FLOOR) * step


class RiskEngine:
    """Assess one proposed trade without mutable state or execution capability."""

    def __init__(self, policy: RiskPolicy) -> None:
        self._policy = policy

    def assess(self, request: TradeRiskRequest) -> RiskAssessment:
        """Return one auditable approval/rejection; every uncertainty rejects."""

        codes: list[RiskRejectionCode] = []
        decision = request.decision
        account = request.account
        instrument = request.instrument
        policy = self._policy

        if request.kill_switch_active:
            codes.append(RiskRejectionCode.KILL_SWITCH_ACTIVE)
        if not request.system_ready:
            codes.append(RiskRejectionCode.SYSTEM_NOT_READY)
        if not request.database_available:
            codes.append(RiskRejectionCode.DATABASE_UNAVAILABLE)
        if not request.broker_connected:
            codes.append(RiskRejectionCode.BROKER_DISCONNECTED)
        if not request.position_reconciled:
            codes.append(RiskRejectionCode.POSITION_NOT_RECONCILED)
        if not request.market_open:
            codes.append(RiskRejectionCode.MARKET_CLOSED)
        if not request.session_allowed:
            codes.append(RiskRejectionCode.SESSION_BLOCKED)
        if request.news_blocked:
            codes.append(RiskRejectionCode.NEWS_BLOCK)
        if decision.action is Action.HOLD:
            codes.append(RiskRejectionCode.RISK_INPUT_INCOMPLETE)
        if request.assessed_at >= decision.expires_at:
            codes.append(RiskRejectionCode.DECISION_EXPIRED)
        if account.reconciled_at > request.assessed_at:
            codes.append(RiskRejectionCode.RISK_INPUT_INCOMPLETE)
        elif request.assessed_at - account.reconciled_at > policy.snapshot_max_age:
            codes.append(RiskRejectionCode.STALE_DATA)
        if decision.symbol != instrument.symbol:
            codes.append(RiskRejectionCode.UNKNOWN_SYMBOL_SPEC)
        if request.observed_spread_points > policy.max_spread_points:
            codes.append(RiskRejectionCode.SPREAD_TOO_WIDE)
        if request.expected_slippage_points > policy.max_slippage_points:
            codes.append(RiskRejectionCode.SLIPPAGE_RISK_HIGH)
        reconciled_risk = sum(
            (item.monetary_risk for item in request.existing_exposures), Decimal("0")
        )
        if reconciled_risk != account.open_risk:
            codes.append(RiskRejectionCode.RISK_INPUT_INCOMPLETE)

        direction = Decimal("1") if decision.action is Action.BUY else Decimal("-1")
        stop_distance = (request.entry - request.stop_loss) * direction
        if stop_distance <= 0:
            codes.append(RiskRejectionCode.INVALID_STOP_LOSS)
        stop_ticks = abs(request.entry - request.stop_loss) / instrument.tick_size
        if stop_ticks < policy.min_stop_ticks:
            codes.append(RiskRejectionCode.STOP_DISTANCE_TOO_SMALL)
        if stop_ticks > policy.max_stop_ticks:
            codes.append(RiskRejectionCode.STOP_DISTANCE_TOO_LARGE)

        round_trip_cost = (
            Decimal(request.observed_spread_points + request.expected_slippage_points)
            * instrument.point
            + request.commission_price_cost
            + request.swap_price_cost
        )
        if request.take_profit is None and request.exit_policy is None:
            codes.append(RiskRejectionCode.RISK_INPUT_INCOMPLETE)
        if request.take_profit is not None and stop_distance > 0:
            reward = (request.take_profit - request.entry) * direction - round_trip_cost
            effective_risk = abs(request.entry - request.stop_loss) + round_trip_cost
            reward_risk = reward / effective_risk
            if reward <= 0 or reward_risk < policy.min_reward_risk:
                codes.append(RiskRejectionCode.LOW_REWARD_RISK)

        if _drawdown(account.daily_peak_equity, account.equity) >= policy.max_daily_loss_fraction:
            codes.append(RiskRejectionCode.DAILY_LOSS_LIMIT)
        if _drawdown(account.account_peak_equity, account.equity) >= policy.max_drawdown_fraction:
            codes.append(RiskRejectionCode.DRAWDOWN_LIMIT)
        if account.open_positions >= policy.max_open_positions:
            codes.append(RiskRejectionCode.POSITION_LIMIT)
        if account.consecutive_losses >= policy.max_consecutive_losses:
            if account.last_loss_at is None or account.last_loss_at > request.assessed_at:
                codes.append(RiskRejectionCode.RISK_INPUT_INCOMPLETE)
            elif request.assessed_at < account.last_loss_at + policy.cooldown:
                codes.append(RiskRejectionCode.CONSECUTIVE_LOSS_COOLDOWN)

        per_trade_budget = account.equity * policy.risk_per_trade
        portfolio_limit = account.equity * policy.max_portfolio_open_risk_fraction
        portfolio_remaining = max(Decimal("0"), portfolio_limit - account.open_risk)
        risk_budget = min(per_trade_budget, portfolio_remaining)
        if risk_budget <= 0:
            codes.append(RiskRejectionCode.PORTFOLIO_RISK_LIMIT)

        cost_ticks = round_trip_cost / instrument.tick_size
        loss_per_lot = (stop_ticks + cost_ticks) * instrument.tick_value
        raw_volume = risk_budget / loss_per_lot if loss_per_lot > 0 else Decimal("0")
        volume = min(_rounded_down(raw_volume, instrument.volume_step), instrument.volume_max)
        if volume < instrument.volume_min:
            codes.append(RiskRejectionCode.VOLUME_BELOW_MINIMUM)
        actual_risk = loss_per_lot * volume if volume >= instrument.volume_min else Decimal("0")
        if actual_risk > per_trade_budget:
            codes.append(RiskRejectionCode.TRADE_RISK_LIMIT)
        required_margin = request.margin_per_lot * volume
        retained_margin = account.free_margin * policy.minimum_margin_buffer_fraction
        if required_margin > account.free_margin - retained_margin:
            codes.append(RiskRejectionCode.INSUFFICIENT_MARGIN)

        if volume >= instrument.volume_min and decision.action is not Action.HOLD:
            candidate = CandidateExposure(
                symbol=decision.symbol,
                strategy=request.strategy_key,
                asset_class=instrument.asset_class,
                action=decision.action,
                volume=volume,
                contract_size=instrument.contract_size,
                price=request.entry,
                monetary_risk=actual_risk,
            )
            exposure_codes = {
                "GROSS": RiskRejectionCode.PORTFOLIO_RISK_LIMIT,
                "SYMBOL": RiskRejectionCode.SYMBOL_RISK_LIMIT,
                "STRATEGY": RiskRejectionCode.STRATEGY_RISK_LIMIT,
                "ASSET": RiskRejectionCode.PORTFOLIO_RISK_LIMIT,
                "CURRENCY": RiskRejectionCode.CURRENCY_EXPOSURE_LIMIT,
            }
            try:
                breaches = limit_breaches(
                    with_candidate(request.existing_exposures, candidate), request.exposure_limits
                )
            except ValueError:
                codes.append(RiskRejectionCode.RISK_INPUT_INCOMPLETE)
                breaches = ()
            for breach in breaches:
                prefix = breach.split("_", maxsplit=1)[0]
                codes.append(exposure_codes[prefix])

        canonical_codes = tuple(sorted({code.value for code in codes}))
        approved = not canonical_codes
        if not approved:
            volume = Decimal("0")
            actual_risk = Decimal("0")
        identity = json.dumps(
            {
                "assessed_at": request.assessed_at.isoformat(),
                "decision_id": str(decision.id),
                "entry": str(request.entry),
                "commission_price_cost": str(request.commission_price_cost),
                "exit_policy_version": request.exit_policy.version if request.exit_policy else None,
                "exit_policy_max_holding_seconds": (
                    request.exit_policy.max_holding_period.total_seconds()
                )
                if request.exit_policy
                else None,
                "policy_version": policy.version,
                "rejection_codes": canonical_codes,
                "risk_amount": str(actual_risk),
                "stop_loss": str(request.stop_loss),
                "swap_price_cost": str(request.swap_price_cost),
                "symbol": decision.symbol,
                "take_profit": (
                    str(request.take_profit) if request.take_profit is not None else None
                ),
                "volume": str(volume),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return RiskAssessment(
            id=uuid5(NAMESPACE_URL, identity),
            decision_id=decision.id,
            policy_version=policy.version,
            approved=approved,
            rejection_codes=canonical_codes,
            risk_amount=actual_risk,
            volume=volume,
            stop_loss=request.stop_loss if approved else None,
            take_profit=request.take_profit if approved else None,
            created_at=request.assessed_at,
        )
