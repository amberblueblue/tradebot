from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


LONG = "LONG"
HOLD = "HOLD"
CLOSE = "CLOSE"
CLOSE_FULL = "CLOSE_FULL"
CLOSE_PARTIAL_30 = "CLOSE_PARTIAL_30"
CLOSE_PARTIAL_50 = "CLOSE_PARTIAL_50"


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    action: str
    reason: str
    trend_timeframe: str
    signal_timeframe: str
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FuturesStrategy(Protocol):
    name: str
    paper_only: bool

    def generate_signal(
        self,
        *,
        symbol: str,
        trend_klines: list[Any],
        signal_klines: list[Any],
        mark_price: float,
        funding_rate: float,
        trend_timeframe: str,
        signal_timeframe: str,
        max_funding_rate_abs: float,
    ) -> StrategySignal:
        ...
