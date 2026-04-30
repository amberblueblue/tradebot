from __future__ import annotations

from typing import Any

from futures_bot.strategy.base import CLOSE, HOLD, LONG, StrategySignal
from futures_bot.strategy.trend_long import _ema, _klines_to_candles, _macd


class TrendLongTestStrategy:
    """Looser LONG-only strategy for paper-loop validation."""

    name = "trend_long_test"
    paper_only = True

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
        signal_candles = _klines_to_candles(signal_klines)
        metadata: dict[str, Any] = {
            "trend_bars": len(_klines_to_candles(trend_klines)),
            "signal_bars": len(signal_candles),
            "max_funding_rate_abs": max_funding_rate_abs,
            "paper_test_only": True,
        }

        if len(signal_candles) < 50:
            return StrategySignal(
                symbol=symbol,
                action=HOLD,
                reason="insufficient_signal_klines",
                trend_timeframe=trend_timeframe,
                signal_timeframe=signal_timeframe,
                confidence=0.0,
                metadata=metadata,
            )

        signal = _signal_snapshot(signal_candles)
        metadata["signal"] = signal

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

        if signal["close"] < signal["ema44"]:
            return StrategySignal(
                symbol=symbol,
                action=CLOSE,
                reason="test_close_below_ema44",
                trend_timeframe=trend_timeframe,
                signal_timeframe=signal_timeframe,
                confidence=0.65,
                metadata=metadata,
            )

        if signal["macd_hist"] < signal["previous_macd_hist"] < signal["two_back_macd_hist"]:
            return StrategySignal(
                symbol=symbol,
                action=CLOSE,
                reason="test_macd_hist_weakening",
                trend_timeframe=trend_timeframe,
                signal_timeframe=signal_timeframe,
                confidence=0.6,
                metadata=metadata,
            )

        if signal["close"] > signal["ema44"] and signal["macd_hist"] > signal["previous_macd_hist"]:
            return StrategySignal(
                symbol=symbol,
                action=LONG,
                reason="test_long_close_above_ema44_macd_improving",
                trend_timeframe=trend_timeframe,
                signal_timeframe=signal_timeframe,
                confidence=0.7,
                metadata=metadata,
            )

        return StrategySignal(
            symbol=symbol,
            action=HOLD,
            reason="test_entry_conditions_not_met",
            trend_timeframe=trend_timeframe,
            signal_timeframe=signal_timeframe,
            confidence=0.4,
            metadata=metadata,
        )


def _signal_snapshot(candles: list[dict[str, float]]) -> dict[str, Any]:
    closes = [candle["close"] for candle in candles]
    ema44 = _ema(closes, 44)
    macd_line, macd_signal, macd_hist = _macd(closes)
    return {
        "close": closes[-1],
        "ema44": ema44[-1],
        "macd_line": macd_line[-1],
        "macd_signal": macd_signal[-1],
        "macd_hist": macd_hist[-1],
        "previous_macd_hist": macd_hist[-2],
        "two_back_macd_hist": macd_hist[-3],
        "mark_price_context": closes[-1],
    }
