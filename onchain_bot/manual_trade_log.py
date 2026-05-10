from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from onchain_bot.config_loader import load_onchain_symbols_config
from onchain_bot.tx_status import get_tx_status


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANUAL_TRADES_PATH = PROJECT_ROOT / "runtime" / "onchain_manual_trades.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_payload() -> dict[str, Any]:
    return {"trades": []}


def load_manual_trades(path: Path | None = None) -> dict[str, Any]:
    trade_path = path or DEFAULT_MANUAL_TRADES_PATH
    if not trade_path.exists():
        return _empty_payload()
    try:
        payload = json.loads(trade_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_payload()
    if not isinstance(payload, dict):
        return _empty_payload()
    trades = payload.get("trades")
    return {"trades": trades if isinstance(trades, list) else []}


def save_manual_trades(payload: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    trade_path = path or DEFAULT_MANUAL_TRADES_PATH
    trades = payload.get("trades")
    if not isinstance(trades, list):
        trades = []
    normalized = {"trades": [trade for trade in trades if isinstance(trade, dict)]}
    try:
        trade_path.parent.mkdir(parents=True, exist_ok=True)
        trade_path.write_text(json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "error": "manual_trade_log_write_failed",
            "message": str(exc),
            "path": str(trade_path),
        }
    return {
        "ok": True,
        "path": str(trade_path),
        "trades_count": len(normalized["trades"]),
    }


def add_manual_trade(
    *,
    symbol: str,
    direction: str,
    tx_hash: str,
    amount: float,
    note: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    normalized_direction = direction.strip().lower()
    normalized_tx_hash = tx_hash.strip()
    if normalized_direction not in {"buy", "sell"}:
        raise ValueError("direction must be buy or sell")
    if not normalized_tx_hash.startswith("0x"):
        raise ValueError("tx_hash must start with 0x")
    if float(amount) <= 0:
        raise ValueError("amount must be greater than 0")

    symbols = load_onchain_symbols_config()
    symbol_config = symbols.get(normalized_symbol)
    if symbol_config is None:
        raise ValueError(f"symbol not configured: {normalized_symbol}")

    now = _now_iso()
    trade = {
        "id": uuid4().hex,
        "symbol": normalized_symbol,
        "direction": normalized_direction,
        "chain_id": symbol_config.chain_id,
        "tx_hash": normalized_tx_hash,
        "amount": float(amount),
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "note": note.strip(),
    }
    payload = load_manual_trades(path)
    payload["trades"].append(trade)
    save_result = save_manual_trades(payload, path)
    return {
        "ok": bool(save_result.get("ok")),
        "trade": trade,
        "error": save_result.get("error"),
        "message": save_result.get("message"),
    }


def refresh_manual_trade_statuses(path: Path | None = None) -> dict[str, Any]:
    payload = load_manual_trades(path)
    updated = 0
    errors: list[dict[str, Any]] = []
    now = _now_iso()
    for trade in payload.get("trades", []):
        if not isinstance(trade, dict):
            continue
        status_result = get_tx_status(str(trade.get("chain_id", "")), str(trade.get("tx_hash", "")))
        trade["status"] = status_result.get("status", "unknown")
        trade["status_reason"] = status_result.get("reason")
        trade["status_checked_at"] = status_result.get("checked_at")
        trade["updated_at"] = now
        updated += 1
        if not status_result.get("ok"):
            errors.append(
                {
                    "id": trade.get("id"),
                    "symbol": trade.get("symbol"),
                    "reason": status_result.get("reason"),
                }
            )
    save_result = save_manual_trades(payload, path)
    return {
        "ok": bool(save_result.get("ok")),
        "updated_count": updated,
        "errors": errors,
        "write_error": save_result.get("error"),
        "message": save_result.get("message"),
    }
