from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    ema_slope_lookback: int = 5
    macd_decay_bars: int = 3
    rsi_overheat: float = 80.0
    entry_cooldown_bars: int = 8
    stop_loss_pct: float = 20.0
    partial1_sell_pct: float = 30.0
    partial2_sell_pct: float = 50.0
    max_hold_bars: int = 72
    min_expected_return: float = 5.0
    big_candle_multiplier: float = 1.5
    big_candle_body_lookback: int = 20
    profit_giveback_ratio: float = 0.5
    profit_protection_trigger_pct: float = 15.0
