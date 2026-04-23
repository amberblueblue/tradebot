from __future__ import annotations

import pandas as pd

from strategy.config import StrategyConfig
from strategy.context import MarketContext
from strategy.position import PositionState
from strategy.risk import FULL_EXIT, HOLD, PARTIAL_EXIT_30, PARTIAL_EXIT_50
from strategy.state import EXIT, IN_POSITION, TREND_OK, StateDecision, update_state


def generate_signal(
    context: MarketContext,
    state: str,
    entry_price: float | None,
    position_state: PositionState | None = None,
    config: StrategyConfig | None = None,
) -> tuple[str, str, float | None, StateDecision]:
    decision = update_state(
        context,
        state,
        entry_price,
        position_state=position_state,
        config=config,
    )

    if state == TREND_OK and decision.new_state == IN_POSITION:
        latest_close = pd.to_numeric(context.df_1h["close"], errors="coerce").iloc[-1]
        if pd.isna(latest_close):
            return "HOLD", state, entry_price, decision
        return "BUY", decision.new_state, float(latest_close), decision

    if state == IN_POSITION and decision.exit_action in {
        PARTIAL_EXIT_30,
        PARTIAL_EXIT_50,
        FULL_EXIT,
    }:
        next_entry_price = None if decision.new_state == EXIT else entry_price
        return decision.exit_action, decision.new_state, next_entry_price, decision

    return HOLD, decision.new_state, entry_price, decision
