from dataclasses import dataclass


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    max_notional: float


def check_risk(
    *,
    account_equity: float,
    proposed_notional: float,
    position_market_value: float,
    max_position_pct: float = 0.10,
    max_daily_loss_pct: float = 0.02,
    daily_pnl: float = 0.0,
) -> RiskDecision:
    if account_equity <= 0:
        return RiskDecision(False, "Account equity must be positive", 0.0)
    if proposed_notional <= 0:
        return RiskDecision(False, "Proposed notional must be positive", 0.0)
    if not 0 < max_position_pct <= 1:
        return RiskDecision(False, "max_position_pct must be between 0 and 1", 0.0)
    if not 0 < max_daily_loss_pct <= 1:
        return RiskDecision(False, "max_daily_loss_pct must be between 0 and 1", 0.0)

    daily_loss_limit = account_equity * max_daily_loss_pct
    if daily_pnl <= -daily_loss_limit:
        return RiskDecision(False, "Daily loss limit reached", 0.0)

    max_position_value = account_equity * max_position_pct
    remaining = max_position_value - max(position_market_value, 0.0)
    if remaining <= 0:
        return RiskDecision(False, "Maximum position size reached", 0.0)

    allowed_notional = min(proposed_notional, remaining)
    return RiskDecision(True, "Risk checks passed", allowed_notional)
