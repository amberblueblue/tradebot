from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategy.config import StrategyConfig
from strategy.context import MarketContext
from strategy.position import PositionState


HOLD = "HOLD"
PARTIAL_EXIT_30 = "PARTIAL_EXIT_30"
PARTIAL_EXIT_50 = "PARTIAL_EXIT_50"
FULL_EXIT = "FULL_EXIT"


@dataclass(frozen=True)
class ExitDecision:
    action: str
    reason_code: str | None = None
    exit_type: str | None = None
    sell_pct: float = 0.0
    current_return: float | None = None
    mfe: float | None = None


def _series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        raise ValueError(f"Missing required columns: ['{column}']")
    return pd.to_numeric(df[column], errors="coerce")


def _latest(series: pd.Series) -> float | None:
    if series.empty:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def detect_head_chop(
    context: MarketContext, config: StrategyConfig | None = None
) -> bool:
    config = config or StrategyConfig()
    df_1h = context.df_1h
    required_columns = {"close", "ema44", "macd_line", "macd_signal", "macd_hist"}
    if not required_columns.issubset(df_1h.columns):
        return False

    if len(df_1h) < max(2, config.macd_decay_bars):
        return False

    close = _series(df_1h, "close")
    ema44 = _series(df_1h, "ema44")
    macd_line = _series(df_1h, "macd_line")
    macd_signal = _series(df_1h, "macd_signal")
    macd_hist = _series(df_1h, "macd_hist")

    latest_close = _latest(close)
    latest_ema44 = _latest(ema44)
    if latest_close is None or latest_ema44 is None or latest_close >= latest_ema44:
        return False

    previous_macd_line = macd_line.iloc[-2]
    previous_macd_signal = macd_signal.iloc[-2]
    latest_macd_line = macd_line.iloc[-1]
    latest_macd_signal = macd_signal.iloc[-1]
    cross_down = bool(
        pd.notna(previous_macd_line)
        and pd.notna(previous_macd_signal)
        and pd.notna(latest_macd_line)
        and pd.notna(latest_macd_signal)
        and previous_macd_line >= previous_macd_signal
        and latest_macd_line < latest_macd_signal
    )

    recent_hist = macd_hist.iloc[-config.macd_decay_bars :]
    hist_decay = bool(
        len(recent_hist) == config.macd_decay_bars
        and recent_hist.notna().all()
        and recent_hist.is_monotonic_decreasing
        and recent_hist.nunique() > 1
    )
    return cross_down or hist_decay


def _exit_metrics(
    position_state: PositionState, context: MarketContext
) -> tuple[float | None, float | None]:
    latest_close = _latest(_series(context.df_1h, "close"))
    if latest_close is None:
        return None, position_state.max_unrealized_return
    return position_state.current_return(latest_close), position_state.max_unrealized_return


def _exit_decision(
    action: str,
    reason_code: str | None,
    exit_type: str | None,
    sell_pct: float,
    position_state: PositionState,
    context: MarketContext,
) -> ExitDecision:
    current_return, mfe = _exit_metrics(position_state, context)
    return ExitDecision(
        action=action,
        reason_code=reason_code,
        exit_type=exit_type,
        sell_pct=sell_pct,
        current_return=current_return,
        mfe=mfe,
    )


def is_stop_loss(
    position_state: PositionState,
    context: MarketContext,
    config: StrategyConfig | None = None,
) -> ExitDecision:
    config = config or StrategyConfig()
    df_1h = context.df_1h
    required_columns = {"open", "close", "ema144"}
    if position_state.entry_price is None or not required_columns.issubset(df_1h.columns):
        return _exit_decision(HOLD, None, None, 0.0, position_state, context)

    open_ = _series(df_1h, "open")
    close = _series(df_1h, "close")
    ema144 = _series(df_1h, "ema144")
    latest_close = _latest(close)
    current_return = position_state.current_return(latest_close) if latest_close is not None else None
    if current_return is not None and current_return <= -(config.stop_loss_pct / 100):
        return _exit_decision(
            FULL_EXIT,
            "HARD_STOP_20",
            "full",
            1.0,
            position_state,
            context,
        )

    if len(df_1h) >= config.big_candle_body_lookback + 1:
        latest_open = _latest(open_)
        latest_ema144 = _latest(ema144)
        body = abs(latest_close - latest_open) if latest_close is not None and latest_open is not None else None
        recent_bodies = (open_ - close).abs().iloc[-(config.big_candle_body_lookback + 1) : -1]
        avg_body = recent_bodies.mean()
        if (
            latest_open is not None
            and latest_close is not None
            and latest_ema144 is not None
            and body is not None
            and pd.notna(avg_body)
            and avg_body > 0
            and latest_close < latest_open
            and latest_close < latest_ema144
            and body > config.big_candle_multiplier * float(avg_body)
        ):
            return _exit_decision(
                FULL_EXIT,
                "BIG_CANDLE_EMA_BREAK",
                "full",
                1.0,
                position_state,
                context,
            )

    if len(df_1h) >= 2:
        previous_close = close.iloc[-2]
        previous_ema144 = ema144.iloc[-2]
        latest_open = open_.iloc[-1]
        latest_close_value = close.iloc[-1]
        latest_ema144 = ema144.iloc[-1]
        if (
            pd.notna(previous_close)
            and pd.notna(previous_ema144)
            and pd.notna(latest_open)
            and pd.notna(latest_close_value)
            and pd.notna(latest_ema144)
            and previous_close < previous_ema144
            and latest_open < latest_ema144
            and latest_close_value < latest_ema144
        ):
            return _exit_decision(
                FULL_EXIT,
                "CONFIRMED_EMA_BREAK",
                "full",
                1.0,
                position_state,
                context,
            )

    return _exit_decision(HOLD, None, None, 0.0, position_state, context)


def is_take_profit(
    position_state: PositionState,
    context: MarketContext,
    config: StrategyConfig | None = None,
) -> ExitDecision:
    config = config or StrategyConfig()
    if position_state.entry_price is None:
        return _exit_decision(HOLD, None, None, 0.0, position_state, context)

    if "rsi" in context.df_1h.columns:
        latest_rsi = _latest(_series(context.df_1h, "rsi"))
        if latest_rsi is not None and latest_rsi > config.rsi_overheat and not position_state.partial1_done:
            return _exit_decision(
                PARTIAL_EXIT_30,
                "RSI_OVERHEAT_PARTIAL",
                "partial",
                config.partial1_sell_pct / 100,
                position_state,
                context,
            )

    from strategy.signals import detect_bearish_divergence

    if detect_bearish_divergence(context) and not position_state.partial2_done:
        return _exit_decision(
            PARTIAL_EXIT_50,
            "MACD_BEAR_DIV_PARTIAL",
            "partial",
            config.partial2_sell_pct / 100,
            position_state,
            context,
        )

    return _exit_decision(HOLD, None, None, 0.0, position_state, context)


def detect_profit_giveback(
    position_state: PositionState,
    current_return: float | None,
    config: StrategyConfig | None = None,
) -> bool:
    config = config or StrategyConfig()
    mfe = position_state.max_unrealized_return
    return bool(
        current_return is not None
        and mfe >= config.profit_protection_trigger_pct / 100
        and current_return <= mfe * (1 - config.profit_giveback_ratio)
    )


def should_force_exit(
    position_state: PositionState,
    context: MarketContext,
    config: StrategyConfig | None = None,
) -> ExitDecision:
    config = config or StrategyConfig()
    if position_state.entry_price is None:
        return _exit_decision(HOLD, None, None, 0.0, position_state, context)

    latest_close = _latest(_series(context.df_1h, "close"))
    current_return = position_state.current_return(latest_close) if latest_close is not None else None
    holding_bars = position_state.holding_bars(context.current_bar_index)
    if (
        holding_bars is not None
        and holding_bars > config.max_hold_bars
        and current_return is not None
        and current_return < config.min_expected_return / 100
    ):
        return _exit_decision(
            FULL_EXIT,
            "TIME_STOP_EXIT",
            "full",
            1.0,
            position_state,
            context,
        )

    if detect_profit_giveback(position_state, current_return, config=config):
        return _exit_decision(
            FULL_EXIT,
            "PROFIT_GIVEBACK_EXIT",
            "full",
            1.0,
            position_state,
            context,
        )

    return _exit_decision(HOLD, None, None, 0.0, position_state, context)


def evaluate_exit(
    position_state: PositionState,
    context: MarketContext,
    config: StrategyConfig | None = None,
) -> ExitDecision:
    config = config or StrategyConfig()
    stop_loss = is_stop_loss(position_state, context, config=config)
    if stop_loss.action == FULL_EXIT:
        return stop_loss

    take_profit = is_take_profit(position_state, context, config=config)
    if take_profit.action in {PARTIAL_EXIT_30, PARTIAL_EXIT_50}:
        return take_profit

    force_exit = should_force_exit(position_state, context, config=config)
    if force_exit.action == FULL_EXIT:
        return force_exit

    return _exit_decision(HOLD, None, None, 0.0, position_state, context)
