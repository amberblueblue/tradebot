from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

    @classmethod
    def from_dict(cls, values: dict[str, Any] | None = None) -> "StrategyConfig":
        if not values:
            return cls()

        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {key: values[key] for key in valid_fields if key in values}
        return cls(**filtered)

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None = None) -> "StrategyConfig":
        if settings is None:
            from config.loader import load_project_config

            settings = load_project_config()

        merged: dict[str, Any] = {}
        merged.update(settings.get("strategy", {}))
        merged.update(settings.get("risk", {}))
        return cls.from_dict(merged)
