from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MarketContext:
    df_1h: pd.DataFrame
    df_4h: pd.DataFrame
    current_bar_index: int
    cooldown_remaining: int = 0

    @property
    def latest_1h(self) -> pd.Series:
        return self.df_1h.iloc[-1]

    @property
    def latest_4h(self) -> pd.Series:
        return self.df_4h.iloc[-1]

    def _value(self, row: pd.Series, column: str) -> float | None:
        value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
        if pd.isna(value):
            return None
        return float(value)

    @property
    def close(self) -> float | None:
        return self._value(self.latest_1h, "close")

    @property
    def ema44(self) -> float | None:
        return self._value(self.latest_1h, "ema44")

    @property
    def macd_line(self) -> float | None:
        return self._value(self.latest_1h, "macd_line")

    @property
    def macd_signal(self) -> float | None:
        return self._value(self.latest_1h, "macd_signal")

    @property
    def macd_hist(self) -> float | None:
        return self._value(self.latest_1h, "macd_hist")

    @property
    def rsi(self) -> float | None:
        return self._value(self.latest_1h, "rsi")

    @property
    def close_4h(self) -> float | None:
        return self._value(self.latest_4h, "close")

    @property
    def ema44_4h(self) -> float | None:
        return self._value(self.latest_4h, "ema44")

    @property
    def ema144_4h(self) -> float | None:
        return self._value(self.latest_4h, "ema144")

    @property
    def macd_line_4h(self) -> float | None:
        return self._value(self.latest_4h, "macd_line")

    @property
    def macd_signal_4h(self) -> float | None:
        return self._value(self.latest_4h, "macd_signal")

    @property
    def macd_hist_4h(self) -> float | None:
        return self._value(self.latest_4h, "macd_hist")
