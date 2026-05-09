from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FUTURES_STRATEGY_SIGNALS_PATH = PROJECT_ROOT / "data" / "futures_strategy_signals.json"


def read_futures_signal(symbol: str) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    if not FUTURES_STRATEGY_SIGNALS_PATH.exists():
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "action": "error",
            "reason": "futures_signal_cache_missing",
            "updated_at": None,
            "error": f"missing {FUTURES_STRATEGY_SIGNALS_PATH}",
        }

    try:
        payload = json.loads(FUTURES_STRATEGY_SIGNALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "action": "error",
            "reason": "futures_signal_cache_read_failed",
            "updated_at": None,
            "error": str(exc),
        }

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "action": "error",
            "reason": "futures_signal_cache_invalid",
            "updated_at": None,
            "error": "futures strategy signal cache must be a mapping",
        }

    signal = payload.get(normalized_symbol)
    if not isinstance(signal, dict):
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "action": "error",
            "reason": "futures_signal_not_found",
            "updated_at": None,
            "error": f"no cached futures signal for {normalized_symbol}",
        }

    return {
        "ok": True,
        "symbol": normalized_symbol,
        "action": signal.get("action"),
        "reason": signal.get("reason"),
        "updated_at": signal.get("updated_at") or signal.get("generated_at") or signal.get("timestamp"),
        "confidence": signal.get("confidence"),
        "trend_timeframe": signal.get("trend_timeframe"),
        "signal_timeframe": signal.get("signal_timeframe"),
        "raw": signal,
        "error": None,
    }


def read_signal_for_mapping(mapping: Any) -> dict[str, Any]:
    if getattr(mapping, "signal_source", "") != "futures":
        return {
            "ok": False,
            "symbol": getattr(mapping, "source_symbol", ""),
            "action": "error",
            "reason": "unsupported_signal_source",
            "updated_at": None,
            "error": f"unsupported signal_source={getattr(mapping, 'signal_source', None)}",
        }
    return read_futures_signal(getattr(mapping, "source_symbol", ""))
