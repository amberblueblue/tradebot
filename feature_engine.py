from __future__ import annotations

import pandas as pd


ATR_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_PERIOD = 14
SWING_ATR_MULTIPLIER = 1.5


def _pivot_high(series: pd.Series) -> pd.Series:
    return (
        (series > series.shift(1))
        & (series > series.shift(2))
        & (series > series.shift(-1))
        & (series > series.shift(-2))
    )


def _pivot_low(series: pd.Series) -> pd.Series:
    return (
        (series < series.shift(1))
        & (series < series.shift(2))
        & (series < series.shift(-1))
        & (series < series.shift(-2))
    )


def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window=ATR_PERIOD, min_periods=ATR_PERIOD).mean()


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=MACD_SIGNAL, adjust=False).mean()
    macd_hist = macd_line - macd_signal
    return macd_line, macd_signal, macd_hist


def _rsi(close: pd.Series) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100).where(avg_loss.ne(0), 100)


def _build_filtered_swings(
    high: pd.Series,
    low: pd.Series,
    atr: pd.Series,
    pivot_high_mask: pd.Series,
    pivot_low_mask: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    swing_high = pd.Series(index=high.index, dtype="float64")
    swing_low = pd.Series(index=low.index, dtype="float64")

    last_type = None
    last_index = None
    last_price = None

    for idx in high.index:
        candidates = []
        if bool(pivot_high_mask.iloc[idx]):
            candidates.append(("high", float(high.iloc[idx])))
        if bool(pivot_low_mask.iloc[idx]):
            candidates.append(("low", float(low.iloc[idx])))

        for candidate_type, candidate_price in candidates:
            if last_type is None:
                if candidate_type == "high":
                    swing_high.iloc[idx] = candidate_price
                else:
                    swing_low.iloc[idx] = candidate_price
                last_type = candidate_type
                last_index = idx
                last_price = candidate_price
                continue

            if candidate_type == last_type:
                should_replace = (
                    candidate_type == "high" and candidate_price > last_price
                ) or (
                    candidate_type == "low" and candidate_price < last_price
                )
                if not should_replace:
                    continue

                if candidate_type == "high":
                    swing_high.iloc[last_index] = pd.NA
                    swing_high.iloc[idx] = candidate_price
                else:
                    swing_low.iloc[last_index] = pd.NA
                    swing_low.iloc[idx] = candidate_price

                last_index = idx
                last_price = candidate_price
                continue

            current_atr = atr.iloc[idx]
            if pd.isna(current_atr):
                continue

            distance = abs(candidate_price - last_price)
            if distance < current_atr * SWING_ATR_MULTIPLIER:
                continue

            if candidate_type == "high":
                swing_high.iloc[idx] = candidate_price
            else:
                swing_low.iloc[idx] = candidate_price

            last_type = candidate_type
            last_index = idx
            last_price = candidate_price

    return swing_high, swing_low


def _swing_structure_flags(
    swing_high: pd.Series, swing_low: pd.Series
) -> tuple[pd.Series, pd.Series]:
    hh = pd.Series(False, index=swing_high.index, dtype="bool")
    hl = pd.Series(False, index=swing_low.index, dtype="bool")

    swing_high_values = swing_high.dropna()
    swing_low_values = swing_low.dropna()

    for i in range(1, len(swing_high_values)):
        current_index = swing_high_values.index[i]
        hh.loc[current_index] = float(swing_high_values.iloc[i]) > float(swing_high_values.iloc[i - 1])

    for i in range(1, len(swing_low_values)):
        current_index = swing_low_values.index[i]
        hl.loc[current_index] = float(swing_low_values.iloc[i]) > float(swing_low_values.iloc[i - 1])

    return hh, hl


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    featured_df = df.copy()
    featured_df = featured_df.sort_values("timestamp").reset_index(drop=True)
    close = pd.to_numeric(featured_df["close"], errors="coerce")
    high = pd.to_numeric(featured_df["high"], errors="coerce")
    low = pd.to_numeric(featured_df["low"], errors="coerce")

    featured_df["ema44"] = close.ewm(span=44, adjust=False).mean()
    featured_df["ema144"] = close.ewm(span=144, adjust=False).mean()
    featured_df["atr"] = _atr_series(high, low, close)
    macd_line, macd_signal, macd_hist = _macd(close)
    featured_df["macd_line"] = macd_line
    featured_df["macd_signal"] = macd_signal
    featured_df["macd_hist"] = macd_hist
    featured_df["rsi"] = _rsi(close)

    pivot_high_mask = _pivot_high(high)
    pivot_low_mask = _pivot_low(low)
    swing_high, swing_low = _build_filtered_swings(
        high=high,
        low=low,
        atr=featured_df["atr"],
        pivot_high_mask=pivot_high_mask,
        pivot_low_mask=pivot_low_mask,
    )

    featured_df["swing_high"] = swing_high
    featured_df["swing_low"] = swing_low
    hh, hl = _swing_structure_flags(swing_high, swing_low)
    featured_df["hh"] = hh
    featured_df["hl"] = hl
    return featured_df
