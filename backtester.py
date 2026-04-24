from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

import feature_engine
import strategy.strategy as strategy
from config.loader import load_backtest_runtime, load_project_config
from data_source import load_csv_data
from observability import metrics as observability_metrics
from observability.event_logger import EventLogger
from observability.report_generator import generate_report
from strategy.config import StrategyConfig
from strategy.context import MarketContext
from strategy.position import PositionState
from strategy.risk import FULL_EXIT, PARTIAL_EXIT_30, PARTIAL_EXIT_50
from strategy.signals import (
    INVALID,
    VALID,
    WARNING,
    is_symbol_valid,
    is_trend_confirm,
)
from strategy.state import IDLE


INITIAL_STATE = IDLE

def load_data(
    file_path: str,
    feature_config: feature_engine.FeatureConfig | dict[str, Any] | None = None,
) -> pd.DataFrame:
    df = load_csv_data(file_path)
    return feature_engine.add_features(df, config=feature_config)


def build_market_context(
    df_1h_window: pd.DataFrame,
    df_4h: pd.DataFrame,
    current_bar_index: int,
    cooldown_remaining: int,
) -> MarketContext | None:
    latest_timestamp = df_1h_window.iloc[-1]["timestamp"]
    df_4h_window = df_4h[df_4h["timestamp"] <= latest_timestamp]
    if df_4h_window.empty:
        return None
    return MarketContext(
        df_1h=df_1h_window,
        df_4h=df_4h_window,
        current_bar_index=current_bar_index,
        cooldown_remaining=cooldown_remaining,
    )


def _swing_structure_state(window: pd.DataFrame) -> str:
    swing_highs = pd.to_numeric(window.get("swing_high"), errors="coerce").dropna()
    swing_lows = pd.to_numeric(window.get("swing_low"), errors="coerce").dropna()
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "INSUFFICIENT_STRUCTURE"

    has_hh = float(swing_highs.iloc[-1]) > float(swing_highs.iloc[-2])
    has_hl = float(swing_lows.iloc[-1]) > float(swing_lows.iloc[-2])
    if has_hh and has_hl:
        return "HH_HL"
    if has_hh:
        return "HH_ONLY"
    if has_hl:
        return "HL_ONLY"
    return "BROKEN_STRUCTURE"


def _float_or_none(value) -> float | None:
    return float(value) if pd.notna(value) else None


def _signal_snapshot(context: MarketContext) -> dict:
    latest_1h = context.latest_1h
    latest_4h = context.latest_4h
    return {
        "ema44_at_signal": _float_or_none(latest_1h.get("ema44")),
        "ema144_4h_at_signal": _float_or_none(latest_4h.get("ema144")),
        "atr_at_signal": _float_or_none(latest_1h.get("atr")),
        "macd_line_at_signal": _float_or_none(latest_1h.get("macd_line")),
        "macd_signal_at_signal": _float_or_none(latest_1h.get("macd_signal")),
        "macd_hist_at_signal": _float_or_none(latest_1h.get("macd_hist")),
        "rsi_at_signal": _float_or_none(latest_1h.get("rsi")),
        "swing_structure_state": _swing_structure_state(context.df_1h),
        "cooldown_remaining": context.cooldown_remaining,
    }


def run_backtest(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    symbol: str = "",
    initial_capital: float = 10000.0,
    report_file: str = "reports/backtest_dashboard.html",
    log_file: str = "logs/backtest_events.json",
) -> tuple[float, int, float]:
    config = config or StrategyConfig.from_settings()
    resolved_symbol = symbol or "UNKNOWN"
    cash = initial_capital
    position_size = 0.0
    entry_value = 0.0
    entry_bar_index = None
    completed_trades = 0
    winning_trades = 0
    symbol_valid_count = 0
    trend_confirm_valid_count = 0
    trend_confirm_warning_count = 0
    trend_confirm_invalid_count = 0
    state = INITIAL_STATE
    entry_price = None
    cooldown_remaining = 0
    position_state = PositionState()
    logger = EventLogger()

    for i in range(len(df_1h)):
        window_1h = df_1h.iloc[: i + 1]
        latest_bar = window_1h.iloc[-1]
        context = build_market_context(window_1h, df_4h, i, cooldown_remaining)
        if context is None:
            continue

        event_base = {
            "timestamp": latest_bar["timestamp"],
            "bar_index": i,
            "symbol": resolved_symbol,
            "side": "LONG",
            "price": float(latest_bar["close"]),
        }
        signal_snapshot = _signal_snapshot(context)

        symbol_valid = is_symbol_valid(context, config=config)
        trend_confirm = is_trend_confirm(context, config=config)
        if symbol_valid:
            symbol_valid_count += 1
        if trend_confirm.status == VALID:
            trend_confirm_valid_count += 1
        elif trend_confirm.status == WARNING:
            trend_confirm_warning_count += 1
        else:
            trend_confirm_invalid_count += 1

        if symbol_valid:
            event_type = "signal_trigger" if trend_confirm.status == VALID else "rejected_signal"
            signal_rejected_reason = None
            if trend_confirm.status == WARNING:
                signal_rejected_reason = "trend_confirm_warning"
            elif trend_confirm.status == INVALID:
                signal_rejected_reason = "trend_confirm_invalid"
            logger.log_event(
                event_type=event_type,
                signal_rejected_reason=signal_rejected_reason,
                trend_status=trend_confirm.status,
                reason_code=trend_confirm.reason_code,
                entry_price=None,
                exit_price=None,
                pnl=None,
                holding_bars=None,
                exit_reason=None,
                **event_base,
                **signal_snapshot,
            )

        current_close = float(window_1h.iloc[-1]["close"])
        if position_size > 0.0:
            position_state.update_mfe(current_close)

        previous_entry_price = entry_price
        action, state, entry_price, decision = strategy.generate_signal(
            context,
            state,
            entry_price,
            position_state=position_state,
            config=config,
        )

        if action == "BUY" and position_size == 0.0 and cash > 0.0:
            position_size = cash / current_close
            entry_value = cash
            entry_bar_index = i
            cash = 0.0
            position_state.start(current_close, i)
            logger.log_event(
                event_type="entry",
                signal_rejected_reason=None,
                trend_status=decision.trend_confirm_status,
                reason_code=decision.reason_code,
                entry_price=current_close,
                exit_price=None,
                pnl=None,
                holding_bars=0,
                exit_reason=None,
                exit_type=None,
                exit_action=None,
                sell_pct=None,
                current_return=None,
                mfe=0.0,
                **event_base,
                **signal_snapshot,
            )
        elif (
            state == "TREND_OK"
            and decision.trend_potential
            and decision.reason_code == "INVALID_ENTRY_COOLDOWN"
        ):
            logger.log_event(
                event_type="rejected_signal",
                signal_rejected_reason="entry_cooldown_active",
                trend_status=decision.trend_confirm_status,
                reason_code=decision.reason_code,
                entry_price=None,
                exit_price=None,
                pnl=None,
                holding_bars=None,
                exit_reason=None,
                exit_type=None,
                exit_action=None,
                sell_pct=None,
                current_return=None,
                mfe=None,
                **event_base,
                **signal_snapshot,
            )
        elif action in {PARTIAL_EXIT_30, PARTIAL_EXIT_50} and position_size > 0.0:
            sell_pct = decision.exit_sell_pct
            sell_size = position_size * sell_pct
            exit_value = sell_size * current_close
            sold_cost_basis = entry_value * sell_pct
            cash += exit_value
            position_size -= sell_size
            entry_value -= sold_cost_basis
            trade_pnl = exit_value - sold_cost_basis
            if action == PARTIAL_EXIT_30:
                position_state.partial1_done = True
            if action == PARTIAL_EXIT_50:
                position_state.partial2_done = True
            logger.log_event(
                event_type="exit",
                signal_rejected_reason=None,
                trend_status=decision.trend_confirm_status,
                reason_code=decision.reason_code,
                entry_price=previous_entry_price,
                exit_price=current_close,
                pnl=trade_pnl,
                exit_reason=decision.reason_code,
                exit_type=decision.exit_type,
                exit_action=action,
                sell_pct=sell_pct * 100,
                current_return=(decision.current_return * 100) if decision.current_return is not None else None,
                mfe=(decision.mfe * 100) if decision.mfe is not None else None,
                holding_bars=position_state.holding_bars(i),
                **event_base,
                **signal_snapshot,
            )
        elif action == FULL_EXIT and position_size > 0.0:
            exit_value = position_size * current_close
            cash += exit_value
            completed_trades += 1
            trade_pnl = exit_value - entry_value
            if exit_value > entry_value:
                winning_trades += 1
            logger.log_event(
                event_type="exit",
                signal_rejected_reason=None,
                trend_status=decision.trend_confirm_status,
                reason_code=decision.reason_code,
                entry_price=previous_entry_price,
                exit_price=current_close,
                pnl=trade_pnl,
                holding_bars=(i - entry_bar_index) if entry_bar_index is not None else None,
                exit_reason=decision.reason_code,
                exit_type=decision.exit_type,
                exit_action=action,
                sell_pct=100.0,
                current_return=(decision.current_return * 100) if decision.current_return is not None else None,
                mfe=(decision.mfe * 100) if decision.mfe is not None else None,
                **event_base,
                **signal_snapshot,
            )
            position_size = 0.0
            entry_value = 0.0
            entry_bar_index = None
            position_state.reset()
            cooldown_remaining = config.entry_cooldown_bars

        if cooldown_remaining > 0 and action != FULL_EXIT:
            cooldown_remaining -= 1

    final_capital = cash
    if position_size > 0.0:
        final_close = float(df_1h.iloc[-1]["close"])
        position_state.update_mfe(final_close)
        exit_value = position_size * final_close
        final_capital = cash + exit_value
        completed_trades += 1
        trade_pnl = exit_value - entry_value
        if exit_value > entry_value:
            winning_trades += 1
        latest_bar = df_1h.iloc[-1]
        logger.log_event(
            timestamp=latest_bar["timestamp"],
            bar_index=len(df_1h) - 1,
            event_type="exit",
            symbol=resolved_symbol,
            side="LONG",
            price=final_close,
            ema44_at_signal=_float_or_none(latest_bar.get("ema44")),
            ema144_4h_at_signal=None,
            atr_at_signal=_float_or_none(latest_bar.get("atr")),
            macd_line_at_signal=_float_or_none(latest_bar.get("macd_line")),
            macd_signal_at_signal=_float_or_none(latest_bar.get("macd_signal")),
            macd_hist_at_signal=_float_or_none(latest_bar.get("macd_hist")),
            rsi_at_signal=_float_or_none(latest_bar.get("rsi")),
            swing_structure_state=_swing_structure_state(df_1h),
            cooldown_remaining=0,
            signal_rejected_reason=None,
            trend_status=None,
            reason_code="EXIT_END_OF_BACKTEST",
            exit_type="full",
            exit_action=FULL_EXIT,
            sell_pct=100.0,
            current_return=((final_close - entry_price) / entry_price * 100) if entry_price else None,
            mfe=position_state.max_unrealized_return * 100,
            entry_price=entry_price,
            exit_price=final_close,
            pnl=trade_pnl,
            holding_bars=(len(df_1h) - 1 - entry_bar_index) if entry_bar_index is not None else None,
            exit_reason="end_of_backtest",
        )

    win_rate = (winning_trades / completed_trades * 100.0) if completed_trades else 0.0
    log_path = logger.save_logs(log_file)
    summary = observability_metrics.calculate_metrics(
        logger.get_events(), initial_capital=initial_capital
    )
    observability_metrics.print_metrics(summary)
    report_path = generate_report(
        df_1h,
        logger.get_events(),
        summary=summary,
        output_path=report_file,
        initial_capital=initial_capital,
    )
    print(f"trend_potential count: {symbol_valid_count}")
    print(f"trend_confirm valid count: {trend_confirm_valid_count}")
    print(f"trend_confirm warning count: {trend_confirm_warning_count}")
    print(f"trend_confirm invalid count: {trend_confirm_invalid_count}")
    print(f"Event logs saved to {log_path}")
    print(f"Dashboard saved to {report_path}")
    return final_capital, completed_trades, win_rate


def main():
    settings = load_project_config()
    runtime_config = load_backtest_runtime(settings)
    strategy_config = StrategyConfig.from_settings(settings)
    feature_config = feature_engine.FeatureConfig.from_dict(settings.get("feature_engine", {}))

    Path(runtime_config.log_file).parent.mkdir(parents=True, exist_ok=True)
    Path(runtime_config.report_file).parent.mkdir(parents=True, exist_ok=True)

    df_1h = load_data(runtime_config.data_file_1h, feature_config=feature_config)
    df_4h = load_data(runtime_config.data_file_4h, feature_config=feature_config)
    final_capital, trade_count, win_rate = run_backtest(
        df_1h,
        df_4h,
        config=strategy_config,
        symbol=runtime_config.symbol,
        initial_capital=runtime_config.initial_capital,
        report_file=runtime_config.report_file,
        log_file=runtime_config.log_file,
    )

    print(f"Final capital: {final_capital:.2f} USDT")
    print(f"Trade count: {trade_count}")
    print(f"Win rate: {win_rate:.2f}%")


if __name__ == "__main__":
    main()
