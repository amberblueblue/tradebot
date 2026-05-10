from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from onchain_bot.wallet_signer import get_rpc_url


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_live_tx_status(chain_id: str, tx_hash: str) -> dict[str, Any]:
    normalized_chain_id = str(chain_id).strip()
    normalized_tx_hash = str(tx_hash).strip()
    if not normalized_chain_id:
        return {
            "ok": False,
            "chain_id": normalized_chain_id,
            "tx_hash": normalized_tx_hash,
            "status": "unknown",
            "reason": "missing_chain_id",
            "checked_at": _now_iso(),
        }
    if not normalized_tx_hash.startswith("0x"):
        return {
            "ok": False,
            "chain_id": normalized_chain_id,
            "tx_hash": normalized_tx_hash,
            "status": "unknown",
            "reason": "invalid_tx_hash",
            "checked_at": _now_iso(),
        }
    rpc_url = get_rpc_url(normalized_chain_id)
    if not rpc_url:
        return {
            "ok": False,
            "chain_id": normalized_chain_id,
            "tx_hash": normalized_tx_hash,
            "status": "unknown",
            "reason": "missing_rpc_url",
            "checked_at": _now_iso(),
        }
    try:
        from web3 import Web3
    except ModuleNotFoundError:
        return {
            "ok": False,
            "chain_id": normalized_chain_id,
            "tx_hash": normalized_tx_hash,
            "status": "unknown",
            "reason": "web3_not_installed",
            "checked_at": _now_iso(),
        }
    try:
        web3 = Web3(Web3.HTTPProvider(rpc_url))
        receipt = web3.eth.get_transaction_receipt(normalized_tx_hash)
    except Exception as exc:
        message = str(exc)
        if "not found" in message.lower() or "not in the chain" in message.lower():
            return {
                "ok": True,
                "chain_id": normalized_chain_id,
                "tx_hash": normalized_tx_hash,
                "status": "pending",
                "reason": "receipt_not_found",
                "checked_at": _now_iso(),
                "receipt": None,
            }
        return {
            "ok": False,
            "chain_id": normalized_chain_id,
            "tx_hash": normalized_tx_hash,
            "status": "unknown",
            "reason": "receipt_query_failed",
            "error": message,
            "checked_at": _now_iso(),
        }
    if receipt is None:
        return {
            "ok": True,
            "chain_id": normalized_chain_id,
            "tx_hash": normalized_tx_hash,
            "status": "pending",
            "reason": "receipt_not_found",
            "checked_at": _now_iso(),
            "receipt": None,
        }
    receipt_status = int(receipt.get("status", 0))
    status = "confirmed" if receipt_status == 1 else "failed"
    return {
        "ok": True,
        "chain_id": normalized_chain_id,
        "tx_hash": normalized_tx_hash,
        "status": status,
        "reason": "ok",
        "checked_at": _now_iso(),
        "receipt": {
            "block_number": receipt.get("blockNumber"),
            "gas_used": receipt.get("gasUsed"),
            "status": receipt_status,
            "transaction_hash": receipt.get("transactionHash").hex()
            if hasattr(receipt.get("transactionHash"), "hex")
            else str(receipt.get("transactionHash")),
        },
    }
