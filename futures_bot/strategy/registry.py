from __future__ import annotations

from futures_bot.strategy.base import FuturesStrategy
from futures_bot.strategy.trend_long import TrendLongStrategy


_STRATEGIES: dict[str, FuturesStrategy] = {}


def register_strategy(strategy: FuturesStrategy) -> None:
    _STRATEGIES[strategy.name] = strategy


def get_strategy(name: str) -> FuturesStrategy:
    strategy_name = name or "trend_long"
    try:
        return _STRATEGIES[strategy_name]
    except KeyError as exc:
        raise ValueError(f"Unknown futures strategy: {strategy_name}") from exc


register_strategy(TrendLongStrategy())
