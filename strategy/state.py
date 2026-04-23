from __future__ import annotations

from dataclasses import dataclass

from strategy.config import StrategyConfig
from strategy.context import MarketContext
from strategy.position import PositionState
from strategy.risk import (
    FULL_EXIT,
    HOLD,
    evaluate_exit,
)
from strategy.signals import (
    INVALID,
    VALID,
    is_trend_confirm,
    is_trend_potential,
)


IDLE = "IDLE"
TREND_OK = "TREND_OK"
WATCHING = TREND_OK
IN_POSITION = "IN_POSITION"
EXIT = "EXIT"


@dataclass(frozen=True)
class StateDecision:
    new_state: str
    trend_potential: bool
    trend_confirm_status: str
    reason_code: str
    entry_allowed: bool
    cooldown_remaining: int
    exit_action: str = HOLD
    exit_type: str | None = None
    exit_sell_pct: float = 0.0
    current_return: float | None = None
    mfe: float | None = None


def update_state(
    context: MarketContext,
    state: str,
    entry_price: float | None,
    position_state: PositionState | None = None,
    config: StrategyConfig | None = None,
) -> StateDecision:
    config = config or StrategyConfig()
    trend_potential = is_trend_potential(context, config=config)
    trend_confirm = is_trend_confirm(context, config=config)
    cooldown_remaining = context.cooldown_remaining

    if state == IDLE:
        if trend_potential:
            return StateDecision(
                new_state=TREND_OK,
                trend_potential=trend_potential,
                trend_confirm_status=trend_confirm.status,
                reason_code=trend_confirm.reason_code,
                entry_allowed=False,
                cooldown_remaining=cooldown_remaining,
            )
        return StateDecision(
            new_state=IDLE,
            trend_potential=False,
            trend_confirm_status=INVALID,
            reason_code="INVALID_4H_TREND",
            entry_allowed=False,
            cooldown_remaining=cooldown_remaining,
        )

    if state == TREND_OK:
        if not trend_potential:
            return StateDecision(
                new_state=IDLE,
                trend_potential=False,
                trend_confirm_status=INVALID,
                reason_code="INVALID_4H_TREND",
                entry_allowed=False,
                cooldown_remaining=cooldown_remaining,
            )
        if cooldown_remaining > 0:
            return StateDecision(
                new_state=TREND_OK,
                trend_potential=True,
                trend_confirm_status=trend_confirm.status,
                reason_code="INVALID_ENTRY_COOLDOWN",
                entry_allowed=False,
                cooldown_remaining=cooldown_remaining,
            )
        if trend_confirm.status == VALID:
            return StateDecision(
                new_state=IN_POSITION,
                trend_potential=True,
                trend_confirm_status=trend_confirm.status,
                reason_code=trend_confirm.reason_code,
                entry_allowed=True,
                cooldown_remaining=0,
            )
        return StateDecision(
            new_state=TREND_OK,
            trend_potential=True,
            trend_confirm_status=trend_confirm.status,
            reason_code=trend_confirm.reason_code,
            entry_allowed=False,
            cooldown_remaining=cooldown_remaining,
        )

    if state == IN_POSITION:
        position_state = position_state or PositionState(entry_price=entry_price)
        exit_decision = evaluate_exit(position_state, context, config=config)
        if exit_decision.action == FULL_EXIT:
            return StateDecision(
                new_state=EXIT,
                trend_potential=trend_potential,
                trend_confirm_status=trend_confirm.status,
                reason_code=exit_decision.reason_code or "EXIT_SIGNAL_SELL",
                entry_allowed=False,
                cooldown_remaining=0,
                exit_action=exit_decision.action,
                exit_type=exit_decision.exit_type,
                exit_sell_pct=exit_decision.sell_pct,
                current_return=exit_decision.current_return,
                mfe=exit_decision.mfe,
            )
        if exit_decision.action != HOLD:
            return StateDecision(
                new_state=IN_POSITION,
                trend_potential=trend_potential,
                trend_confirm_status=trend_confirm.status,
                reason_code=exit_decision.reason_code or trend_confirm.reason_code,
                entry_allowed=False,
                cooldown_remaining=0,
                exit_action=exit_decision.action,
                exit_type=exit_decision.exit_type,
                exit_sell_pct=exit_decision.sell_pct,
                current_return=exit_decision.current_return,
                mfe=exit_decision.mfe,
            )
        return StateDecision(
            new_state=IN_POSITION,
            trend_potential=trend_potential,
            trend_confirm_status=trend_confirm.status,
            reason_code=trend_confirm.reason_code,
            entry_allowed=False,
            cooldown_remaining=0,
        )

    if state == EXIT:
        next_state = TREND_OK if trend_potential else IDLE
        return StateDecision(
            new_state=next_state,
            trend_potential=trend_potential,
            trend_confirm_status=trend_confirm.status if trend_potential else INVALID,
            reason_code=trend_confirm.reason_code if trend_potential else "INVALID_4H_TREND",
            entry_allowed=False,
            cooldown_remaining=cooldown_remaining,
        )

    raise ValueError(f"Unknown state: {state}")
