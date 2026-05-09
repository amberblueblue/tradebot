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


def update_quote_cache(
    symbol: str,
    result: dict[str, Any],
    cache_path: Path | None = None,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    cache = load_quote_cache(cache_path)
    cache[normalized_symbol] = {
        "symbol": normalized_symbol,
        "ok": bool(result.get("ok")),
        "amount_usdt": result.get("amount_usdt"),
        "quoted_at": datetime.now(timezone.utc).isoformat(),
        "chain_id": result.get("chain_id"),
        "token_symbol": result.get("token_symbol"),
        "quote_token_symbol": result.get("quote_token_symbol"),
        "quote": result.get("quote"),
        "parsed_quote": result.get("parsed_quote"),
        "latency_ms": result.get("latency_ms"),
        "error": _quote_error(result),
    }
    save_result = save_quote_cache(cache, cache_path)
    if not save_result.get("ok"):
        cache[normalized_symbol]["cache_error"] = save_result.get("message")
    return cache[normalized_symbol]


def get_cached_quote(
    symbol: str,
    cache_path: Path | None = None,
) -> dict[str, Any] | None:
    normalized_symbol = symbol.strip().upper()
    cached = load_quote_cache(cache_path).get(normalized_symbol)
    return cached if isinstance(cached, dict) else None
