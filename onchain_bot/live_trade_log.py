from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIVE_TRADES_PATH = PROJECT_ROOT / "runtime" / "onchain_live_trades.json"
DEFAULT_LIVE_AUDIT_PATH = PROJECT_ROOT / "runtime" / "onchain_live_audit.log"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_live_trades(path: Path = DEFAULT_LIVE_TRADES_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"trades": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"trades": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("trades"), list):
        return {"trades": []}
    return payload


def save_live_trades(payload: dict[str, Any], path: Path = DEFAULT_LIVE_TRADES_PATH) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {"trades": []}
    trades = payload.get("trades")
    if not isinstance(trades, list):
        payload["trades"] = []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "path": str(path), "trades_count": len(payload["trades"])}


def append_live_trade(record: dict[str, Any], path: Path = DEFAULT_LIVE_TRADES_PATH) -> dict[str, Any]:
    payload = load_live_trades(path)
    now = _now_iso()
    safe_record = {
        "id": record.get("id") or str(uuid.uuid4()),
        "symbol": record.get("symbol"),
        "direction": record.get("direction"),
        "chain_id": str(record.get("chain_id") or ""),
        "tx_hash": record.get("tx_hash"),
        "status": record.get("status", "pending"),
        "amount": record.get("amount"),
        "quote": record.get("quote"),
        "parsed_quote": record.get("parsed_quote"),
        "risk_result": record.get("risk_result"),
        "created_at": record.get("created_at") or now,
        "updated_at": record.get("updated_at") or now,
        "confirmed_at": record.get("confirmed_at"),
        "failed_at": record.get("failed_at"),
        "error": record.get("error"),
    }
    payload["trades"].append(safe_record)
    return save_live_trades(payload, path)


def update_live_trade_status(
    tx_hash: str,
    status: str,
    details: dict[str, Any] | None = None,
    path: Path = DEFAULT_LIVE_TRADES_PATH,
) -> dict[str, Any]:
    payload = load_live_trades(path)
    normalized_tx_hash = str(tx_hash).strip().lower()
    now = _now_iso()
    updated_count = 0
    for trade in payload.get("trades", []):
        if not isinstance(trade, dict):
            continue
        if str(trade.get("tx_hash", "")).strip().lower() != normalized_tx_hash:
            continue
        trade["status"] = status
        trade["updated_at"] = now
        if status == "confirmed":
            trade["confirmed_at"] = now
            trade["error"] = None
        elif status == "failed":
            trade["failed_at"] = now
            trade["error"] = (details or {}).get("reason") or (details or {}).get("error")
        elif details and details.get("error"):
            trade["error"] = details.get("error")
        if details is not None:
            trade["status_details"] = details
        updated_count += 1
    save_result = save_live_trades(payload, path)
    return {**save_result, "updated_count": updated_count}


def _audit_safe(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        blocked_fragments = ("private", "seed", "mnemonic", "signed_tx", "raw_signed", "rawtransaction")
        for key, item in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in blocked_fragments):
                safe[key] = "[redacted]"
                continue
            safe[key] = _audit_safe(item)
        return safe
    if isinstance(value, list):
        return [_audit_safe(item) for item in value]
    return value


def write_live_audit(
    event: str,
    details: dict[str, Any] | None = None,
    path: Path = DEFAULT_LIVE_AUDIT_PATH,
) -> dict[str, Any]:
    entry = {
        "event": event,
        "at": _now_iso(),
        "details": _audit_safe(details or {}),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as audit_file:
            audit_file.write(f"[ONCHAIN_LIVE_AUDIT] {json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)}\n")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "path": str(path)}
