from __future__ import annotations

from typing import Any

from futures_bot.config_loader import load_futures_config, load_futures_strategy_settings
from futures_bot.strategy.base import CLOSE, HOLD, LONG, StrategySignal
from futures_bot.strategy.session_filter import filter_klines_by_session


class TrendLongStrategy:
    name = "trend_long"
    paper_only = False

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
        settings = load_futures_strategy_settings(self.name)
        market_session_filter = _market_session_filter_for_symbol(symbol)
        filtered_trend_klines = filter_klines_by_session(trend_klines, market_session_filter)
        filtered_signal_klines = filter_klines_by_session(signal_klines, market_session_filter)
        trend_candles = _klines_to_candles(filtered_trend_klines)
        signal_candles = _klines_to_candles(filtered_signal_klines)
        metadata: dict[str, Any] = {
            "market_session_filter": market_session_filter,
            "total_bars": len(signal_klines),
            "session_filtered_bars": len(filtered_signal_klines),
            "filtered_out_bars": max(len(signal_klines) - len(filtered_signal_klines), 0),
            "trend_bars": len(trend_candles),
            "signal_bars": len(signal_candles),
            "trend_total_bars": len(trend_klines),
            "trend_session_filtered_bars": len(filtered_trend_klines),
            "trend_filtered_out_bars": max(len(trend_klines) - len(filtered_trend_klines), 0),
            "max_funding_rate_abs": max_funding_rate_abs,
            "strategy_settings": settings,
        }

        if len(trend_candles) < 150 or len(signal_candles) < 60:
            reason = (
                "insufficient_session_bars"
                if market_session_filter != "none"
                else "insufficient_klines"
            )
            return StrategySignal(
                symbol=symbol,
                action=HOLD,
                reason=reason,
                trend_timeframe=trend_timeframe,
                signal_timeframe=signal_timeframe,
                confidence=0.0,
                metadata=metadata,
            )

        trend = _trend_snapshot(trend_candles, settings)
        signal = _signal_snapshot(signal_candles, settings)
        metadata.update({"trend": trend, "signal": signal})

        if abs(funding_rate) > max_funding_rate_abs:
            return StrategySignal(
                symbol=symbol,
                action=HOLD,
                reason="funding_rate_exceeds_max_abs",
                trend_timeframe=trend_timeframe,
                signal_timeframe=signal_timeframe,
                confidence=0.2,
                metadata=metadata,
            )

        if not trend["bullish"]:
            return StrategySignal(
                symbol=symbol,
                action=CLOSE,
                reason="trend_filter_not_bullish",
                trend_timeframe=trend_timeframe,
                signal_timeframe=signal_timeframe,
                confidence=0.65,
                metadata=metadata,
            )

        close_triggered = (
            signal["close"] < signal["ema44"]
            or (
                signal["macd_line"] < signal["macd_signal"]
                and signal["macd_hist"] < signal["previous_macd_hist"]
            )
        )
        if close_triggered:
            return StrategySignal(
                symbol=symbol,
                action=CLOSE,
                reason="signal_timeframe_momentum_weak",
                trend_timeframe=trend_timeframe,
                signal_timeframe=signal_timeframe,
                confidence=0.7,
                metadata=metadata,
            )

        long_triggered = (
            signal["close"] > signal["ema44"]
            and signal["ema44"] > signal["ema144"]
            and signal["macd_line"] > signal["macd_signal"]
            and signal["macd_hist"] >= signal["previous_macd_hist"]
            and signal["rsi"] < float(settings["max_rsi"])
            and signal["rsi"] >= float(settings["min_rsi"])
            and mark_price >= signal["ema_fast"] * 0.995
        )
        if long_triggered:
            return StrategySignal(
                symbol=symbol,
                action=LONG,
                reason="trend_long_entry",
                trend_timeframe=trend_timeframe,
                signal_timeframe=signal_timeframe,
                confidence=0.8,
                metadata=metadata,
            )

        return StrategySignal(
            symbol=symbol,
            action=HOLD,
            reason="entry_conditions_not_met",
            trend_timeframe=trend_timeframe,
            signal_timeframe=signal_timeframe,
            confidence=0.5,
            metadata=metadata,
        )


def _market_session_filter_for_symbol(symbol: str) -> str:
    try:
        config = load_futures_config()
    except Exception:
        return "none"
    symbol_config = config.symbols.get(symbol)
    if symbol_config is None:
        return "none"
    return symbol_config.market_session_filter


def _klines_to_candles(klines: list[Any]) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    for kline in klines:
        if not isinstance(kline, (list, tuple)) or len(kline) < 6:
            continue
        try:
            candles.append(
                {
                    "open": float(kline[1]),
                    "high": float(kline[2]),
                    "low": float(kline[3]),
                    "close": float(kline[4]),
                    "volume": float(kline[5]),
                }
            )
        except (TypeError, ValueError):
            continue
    return candles


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def _rsi(values: list[float], period: int = 14) -> list[float]:
    if len(values) < period + 1:
        return []
    rsis: list[float] = []
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsis.append(_rsi_value(avg_gain, avg_loss))
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rsis.append(_rsi_value(avg_gain, avg_loss))
    return rsis


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(
    values: list[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[list[float], list[float], list[float]]:
    ema12 = _ema(values, fast_period)
    ema26 = _ema(values, slow_period)
    macd_line = [fast - slow for fast, slow in zip(ema12, ema26)]
    macd_signal = _ema(macd_line, signal_period)
    macd_hist = [line - signal for line, signal in zip(macd_line, macd_signal)]
    return macd_line, macd_signal, macd_hist


def _trend_snapshot(candles: list[dict[str, float]], settings: dict[str, int | float]) -> dict[str, Any]:
    closes = [candle["close"] for candle in candles]
    ema_fast = _ema(closes, int(settings["ema_fast"]))
    ema_slow = _ema(closes, int(settings["ema_slow"]))
    macd_line, macd_signal, macd_hist = _macd(
        closes,
        int(settings["macd_fast"]),
        int(settings["macd_slow"]),
        int(settings["macd_signal"]),
    )
    slope_lookback = 5
    latest = {
        "close": closes[-1],
        "ema44": ema_fast[-1],
        "ema144": ema_slow[-1],
        "ema_fast": ema_fast[-1],
        "ema_slow": ema_slow[-1],
        "ema44_previous": ema_fast[-slope_lookback],
        "ema_fast_previous": ema_fast[-slope_lookback],
        "macd_line": macd_line[-1],
        "macd_signal": macd_signal[-1],
        "macd_hist": macd_hist[-1],
    }
    latest["bullish"] = bool(
        latest["ema_fast"] > latest["ema_slow"]
        and latest["ema_fast"] > latest["ema_fast_previous"]
        and latest["close"] > latest["ema_fast"]
        and latest["macd_line"] > latest["macd_signal"]
        and latest["macd_hist"] >= 0
    )
    return latest


def _signal_snapshot(candles: list[dict[str, float]], settings: dict[str, int | float]) -> dict[str, Any]:
    closes = [candle["close"] for candle in candles]
    ema_fast = _ema(closes, int(settings["ema_fast"]))
    ema_slow = _ema(closes, int(settings["ema_slow"]))
    macd_line, macd_signal, macd_hist = _macd(
        closes,
        int(settings["macd_fast"]),
        int(settings["macd_slow"]),
        int(settings["macd_signal"]),
    )
    rsi_values = _rsi(closes, int(settings["rsi_period"]))
    return {
        "close": closes[-1],
        "ema44": ema_fast[-1],
        "ema144": ema_slow[-1],
        "ema_fast": ema_fast[-1],
        "ema_slow": ema_slow[-1],
        "macd_line": macd_line[-1],
        "macd_signal": macd_signal[-1],
        "macd_hist": macd_hist[-1],
        "previous_macd_hist": macd_hist[-2],
        "rsi": rsi_values[-1] if rsi_values else 50.0,
    }
