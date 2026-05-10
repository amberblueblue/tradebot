from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIVE_TRADES_PATH = PROJECT_ROOT / "runtime" / "onchain_live_trades.json"


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


def append_live_trade(record: dict[str, Any], path: Path = DEFAULT_LIVE_TRADES_PATH) -> dict[str, Any]:
    payload = load_live_trades(path)
    safe_record = {
        "symbol": record.get("symbol"),
        "direction": record.get("direction"),
        "amount": record.get("amount"),
        "tx_hash": record.get("tx_hash"),
        "status": record.get("status", "submitted"),
        "created_at": record.get("created_at") or _now_iso(),
        "quote": record.get("quote"),
        "parsed_quote": record.get("parsed_quote"),
        "risk_result": record.get("risk_result"),
    }
    payload["trades"].append(safe_record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "path": str(path), "trades_count": len(payload["trades"])}
