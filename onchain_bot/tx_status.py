from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def get_tx_status(chain_id: str, tx_hash: str) -> dict[str, Any]:
    normalized_chain_id = str(chain_id).strip()
    normalized_tx_hash = str(tx_hash).strip()
    if not normalized_chain_id:
        return {
            "ok": False,
            "chain_id": normalized_chain_id,
            "tx_hash": normalized_tx_hash,
            "status": "unknown",
            "reason": "missing_chain_id",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    if not normalized_tx_hash.startswith("0x"):
        return {
            "ok": False,
            "chain_id": normalized_chain_id,
            "tx_hash": normalized_tx_hash,
            "status": "unknown",
            "reason": "invalid_tx_hash",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "ok": True,
        "chain_id": normalized_chain_id,
        "tx_hash": normalized_tx_hash,
        "status": "unknown",
        "reason": "tx_status_not_implemented",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "query_only": True,
    }
