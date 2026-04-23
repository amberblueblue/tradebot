from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategy.config import StrategyConfig
from strategy.context import MarketContext
from strategy.risk import detect_head_chop


PULLBACK_THRESHOLD = 0.01


VALID = "valid"
WARNING = "warning"
INVALID = "invalid"


@dataclass(frozen=True)
class TrendConfirmResult:
    status: str
    reason_code: str


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


def _recent_price_pivots(series: pd.Series) -> pd.Index:
    if len(series) < 5:
        return pd.Index([])
    mask = (
        (series > series.shift(1))
        & (series > series.shift(2))
        & (series > series.shift(-1))
        & (series > series.shift(-2))
    )
    return series[mask.fillna(False)].index


def is_symbol_valid(context: MarketContext, config: StrategyConfig | None = None) -> bool:
    return is_trend_potential(context, config=config)


def is_trend_potential(
    context: MarketContext, config: StrategyConfig | None = None
) -> bool:
    config = config or StrategyConfig()
    df_4h = context.df_4h
    required_columns = {
        "close",
        "ema44",
        "ema144",
        "macd_line",
        "macd_signal",
        "macd_hist",
    }
    if not required_columns.issubset(df_4h.columns):
        return False

    minimum_bars = max(144, config.ema_slope_lookback + 1)
    if len(df_4h) < minimum_bars:
        return False

    close_4h = _series(df_4h, "close")
    ema44_4h = _series(df_4h, "ema44")
    ema144_4h = _series(df_4h, "ema144")
    macd_line_4h = _series(df_4h, "macd_line")
    macd_signal_4h = _series(df_4h, "macd_signal")
    macd_hist_4h = _series(df_4h, "macd_hist")

    latest_close = _latest(close_4h)
    latest_ema44 = _latest(ema44_4h)
    latest_ema144 = _latest(ema144_4h)
    latest_macd_line = _latest(macd_line_4h)
    latest_macd_signal = _latest(macd_signal_4h)
    latest_macd_hist = _latest(macd_hist_4h)
    previous_ema44 = _latest(ema44_4h.iloc[: -config.ema_slope_lookback])

    values = [
        latest_close,
        latest_ema44,
        latest_ema144,
        latest_macd_line,
        latest_macd_signal,
        latest_macd_hist,
        previous_ema44,
    ]
    if any(value is None for value in values):
        return False

    return bool(
        latest_ema44 > latest_ema144
        and latest_ema44 > previous_ema44
        and latest_close > latest_ema44
        and latest_macd_line > latest_macd_signal
        and latest_macd_hist >= 0
    )


def detect_bearish_divergence(context: MarketContext) -> bool:
    df_1h = context.df_1h
    required_columns = {"high", "macd_line"}
    if not required_columns.issubset(df_1h.columns):
        return False

    high = _series(df_1h, "high")
    macd_line = _series(df_1h, "macd_line")
    pivot_indexes = _recent_price_pivots(high)
    if len(pivot_indexes) < 2:
        return False

    previous_idx = pivot_indexes[-2]
    latest_idx = pivot_indexes[-1]

    previous_high = high.iloc[previous_idx]
    latest_high = high.iloc[latest_idx]
    previous_macd = macd_line.iloc[previous_idx]
    latest_macd = macd_line.iloc[latest_idx]

    if pd.isna(previous_high) or pd.isna(latest_high) or pd.isna(previous_macd) or pd.isna(latest_macd):
        return False

    return bool(latest_high > previous_high and latest_macd <= previous_macd)


def is_trend_confirm(
    context: MarketContext, config: StrategyConfig | None = None
) -> TrendConfirmResult:
    config = config or StrategyConfig()
    df_1h = context.df_1h
    required_columns = {
        "close",
        "ema44",
        "macd_line",
        "macd_signal",
        "macd_hist",
        "rsi",
    }
    if not required_columns.issubset(df_1h.columns):
        return TrendConfirmResult(status=INVALID, reason_code="INVALID_MISSING_FEATURES")

    if len(df_1h) < max(5, config.macd_decay_bars):
        return TrendConfirmResult(status=INVALID, reason_code="INVALID_INSUFFICIENT_BARS")

    if detect_bearish_divergence(context):
        return TrendConfirmResult(status=INVALID, reason_code="INVALID_BEAR_DIV")

    if detect_head_chop(context, config=config):
        return TrendConfirmResult(status=INVALID, reason_code="INVALID_HEAD_CHOP")

    macd_line = _series(df_1h, "macd_line")
    macd_signal = _series(df_1h, "macd_signal")
    macd_hist = _series(df_1h, "macd_hist")
    rsi = _series(df_1h, "rsi")

    latest_macd_line = _latest(macd_line)
    latest_macd_signal = _latest(macd_signal)
    latest_macd_hist = _latest(macd_hist)
    previous_macd_hist = _latest(macd_hist.iloc[:-1])
    latest_rsi = _latest(rsi)

    if any(
        value is None
        for value in [
            latest_macd_line,
            latest_macd_signal,
            latest_macd_hist,
            previous_macd_hist,
            latest_rsi,
        ]
    ):
        return TrendConfirmResult(status=INVALID, reason_code="INVALID_MISSING_VALUES")

    momentum_ok = bool(
        latest_macd_line > latest_macd_signal
        or latest_macd_hist > previous_macd_hist
    )
    if not momentum_ok and latest_rsi >= config.rsi_overheat:
        return TrendConfirmResult(
            status=WARNING,
            reason_code="WARN_MACD_WEAK_RSI_OVERHEAT",
        )
    if not momentum_ok:
        return TrendConfirmResult(status=WARNING, reason_code="WARN_MACD_WEAK")
    if latest_rsi >= config.rsi_overheat:
        return TrendConfirmResult(status=WARNING, reason_code="WARN_RSI_OVERHEAT")
    return TrendConfirmResult(status=VALID, reason_code="VALID_MACD_EXPANSION")


def is_pullback_entry(context: MarketContext) -> bool:
    df_1h = context.df_1h
    if len(df_1h) < 44:
        return False

    close = _series(df_1h, "close")
    ema44 = _series(df_1h, "ema44")

    latest_close = _latest(close)
    latest_ema44 = _latest(ema44)
    if latest_close is None or latest_ema44 is None or latest_ema44 == 0:
        return False

    distance_ratio = abs(latest_close - latest_ema44) / latest_ema44
    return bool(distance_ratio <= PULLBACK_THRESHOLD)
