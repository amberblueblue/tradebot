from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PositionState:
    entry_price: float | None = None
    entry_bar_index: int | None = None
    partial1_done: bool = False
    partial2_done: bool = False
    max_unrealized_return: float = 0.0

    def reset(self) -> None:
        self.entry_price = None
        self.entry_bar_index = None
        self.partial1_done = False
        self.partial2_done = False
        self.max_unrealized_return = 0.0

    def start(self, entry_price: float, entry_bar_index: int) -> None:
        self.entry_price = entry_price
        self.entry_bar_index = entry_bar_index
        self.partial1_done = False
        self.partial2_done = False
        self.max_unrealized_return = 0.0

    def holding_bars(self, current_bar_index: int) -> int | None:
        if self.entry_bar_index is None:
            return None
        return current_bar_index - self.entry_bar_index

    def current_return(self, current_price: float) -> float | None:
        if self.entry_price is None or self.entry_price == 0:
            return None
        return (current_price - self.entry_price) / self.entry_price

    def update_mfe(self, current_price: float) -> None:
        current_return = self.current_return(current_price)
        if current_return is not None:
            self.max_unrealized_return = max(self.max_unrealized_return, current_return)
