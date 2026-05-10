from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUOTE_CACHE_PATH = PROJECT_ROOT / "runtime" / "onchain_quote_cache.json"


def load_quote_cache(cache_path: Path | None = None) -> dict[str, Any]:
    path = cache_path or DEFAULT_QUOTE_CACHE_PATH
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_quote_cache(
    cache: dict[str, Any],
    cache_path: Path | None = None,
) -> dict[str, Any]:
    path = cache_path or DEFAULT_QUOTE_CACHE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cache, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        return {
            "ok": False,
            "error": "quote_cache_write_failed",
            "message": str(exc),
            "path": str(path),
        }
    return {
        "ok": True,
        "path": str(path),
        "items_count": len(cache),
    }


def _quote_error(result: dict[str, Any]) -> str | None:
    error = result.get("error")
    message = result.get("message")
    if error and message:
        return f"{error}: {message}"
    if error:
        return str(error)
    if message:
        return str(message)
    return None


def _is_directional_quote_cache_entry(entry: Any) -> bool:
    return isinstance(entry, dict) and (
        isinstance(entry.get("buy"), dict)
        or isinstance(entry.get("sell"), dict)
    )


def _quote_cache_entry(
    symbol: str,
    result: dict[str, Any],
    *,
    direction: str,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    return {
        "symbol": normalized_symbol,
        "direction": direction,
        "ok": bool(result.get("ok")),
        "amount_display": result.get("amount_display"),
        "amount_usdt": result.get("amount_usdt"),
        "quoted_at": datetime.now(timezone.utc).isoformat(),
        "chain_id": result.get("chain_id"),
        "from_token_symbol": result.get("from_token_symbol"),
        "to_token_symbol": result.get("to_token_symbol"),
        "from_amount_display": result.get("from_amount_display"),
        "to_amount_display": result.get("to_amount_display"),
        "implied_price": result.get("implied_price"),
        "price_impact_pct": result.get("price_impact_pct"),
        "route": result.get("route"),
        "token_symbol": result.get("token_symbol"),
        "quote_token_symbol": result.get("quote_token_symbol"),
        "quote": result.get("quote"),
        "parsed_quote": result.get("parsed_quote"),
        "latency_ms": result.get("latency_ms"),
        "error": _quote_error(result),
    }


def update_quote_cache(
    symbol: str,
    result: dict[str, Any],
    *,
    direction: str | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    normalized_direction = (direction or str(result.get("direction") or "buy")).strip().lower()
    if normalized_direction not in {"buy", "sell"}:
        normalized_direction = "buy"
    cache = load_quote_cache(cache_path)
    existing = cache.get(normalized_symbol)
    if _is_directional_quote_cache_entry(existing):
        symbol_cache = existing
    elif isinstance(existing, dict):
        symbol_cache = {"buy": existing}
    else:
        symbol_cache = {}

    symbol_cache[normalized_direction] = _quote_cache_entry(
        normalized_symbol,
        result,
        direction=normalized_direction,
    )
    cache[normalized_symbol] = symbol_cache
    save_result = save_quote_cache(cache, cache_path)
    if not save_result.get("ok"):
        symbol_cache[normalized_direction]["cache_error"] = save_result.get("message")
    return symbol_cache[normalized_direction]


def get_cached_quote(
    symbol: str,
    direction: str = "buy",
    cache_path: Path | None = None,
) -> dict[str, Any] | None:
    normalized_symbol = symbol.strip().upper()
    normalized_direction = direction.strip().lower()
    cached = load_quote_cache(cache_path).get(normalized_symbol)
    if not isinstance(cached, dict):
        return None
    if _is_directional_quote_cache_entry(cached):
        directional = cached.get(normalized_direction)
        return directional if isinstance(directional, dict) else None
    if normalized_direction == "buy":
        return cached
    return None
