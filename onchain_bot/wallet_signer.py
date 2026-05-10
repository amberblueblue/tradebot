from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from onchain_bot.config_loader import load_onchain_settings_config
from onchain_bot.live_guard import assert_onchain_live_allowed
from onchain_bot.wallet_guard import (
    ONCHAIN_PRIVATE_KEY_ENV,
    ONCHAIN_WALLET_ADDRESS_ENV,
    check_wallet_environment,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = PROJECT_ROOT / ".env"
RPC_ENV_BY_CHAIN = {
    "ethereum": "ONCHAIN_RPC_ETHEREUM",
    "base": "ONCHAIN_RPC_BASE",
    "arbitrum": "ONCHAIN_RPC_ARBITRUM",
}
RPC_CHAIN_ID_TO_NAME = {
    "1": "ethereum",
    "8453": "base",
    "42161": "arbitrum",
}


try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(dotenv_path: str | os.PathLike[str] | None = None) -> bool:
        env_path = os.fspath(dotenv_path or DOTENV_PATH)
        if not os.path.exists(env_path):
            return False
        try:
            with open(env_path, "r", encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            return True
        except OSError:
            return False


load_dotenv(DOTENV_PATH)


def _chain_name(chain_id: str | int | None) -> str:
    return RPC_CHAIN_ID_TO_NAME.get(str(chain_id or ""), "ethereum")


def get_rpc_url(chain_id: str | int | None, chain_name: str | None = None) -> str | None:
    settings = load_onchain_settings_config()
    normalized_chain = (chain_name or _chain_name(chain_id)).strip().lower()
    env_key = RPC_ENV_BY_CHAIN.get(normalized_chain)
    if env_key and os.environ.get(env_key):
        return str(os.environ[env_key])
    configured = {
        "ethereum": settings.rpc_ethereum,
        "base": settings.rpc_base,
        "arbitrum": settings.rpc_arbitrum,
    }.get(normalized_chain)
    return configured or None


def load_wallet_from_env() -> dict[str, Any]:
    return {
        "wallet_address": os.environ.get(ONCHAIN_WALLET_ADDRESS_ENV) or None,
        "private_key": os.environ.get(ONCHAIN_PRIVATE_KEY_ENV) or None,
        "wallet_address_configured": bool(os.environ.get(ONCHAIN_WALLET_ADDRESS_ENV)),
        "private_key_configured": bool(os.environ.get(ONCHAIN_PRIVATE_KEY_ENV)),
    }


def _web3_for_tx(tx: dict[str, Any]) -> tuple[Any | None, str | None]:
    rpc_url = get_rpc_url(tx.get("chain_id"), tx.get("chain_name"))
    if not rpc_url:
        return None, "missing_rpc_url"
    try:
        from web3 import Web3
    except ModuleNotFoundError:
        return None, "web3_not_installed"
    return Web3(Web3.HTTPProvider(rpc_url)), None


def _int_or_zero(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, str) and value.startswith("0x"):
        return int(value, 16)
    return int(value)


def _build_evm_transaction(tx: dict[str, Any], wallet_address: str, web3: Any) -> dict[str, Any]:
    chain_id = _int_or_zero(tx.get("chain_id"))
    network_chain_id = int(web3.eth.chain_id)
    if chain_id and network_chain_id != chain_id:
        raise ValueError("chain_id_mismatch")
    transaction = {
        "chainId": chain_id or network_chain_id,
        "to": tx.get("to"),
        "data": tx.get("data") or "0x",
        "value": _int_or_zero(tx.get("value")),
        "nonce": web3.eth.get_transaction_count(wallet_address),
    }
    if tx.get("gas"):
        transaction["gas"] = _int_or_zero(tx.get("gas"))
    else:
        transaction["gas"] = web3.eth.estimate_gas({**transaction, "from": wallet_address})
    if tx.get("gas_price"):
        transaction["gasPrice"] = _int_or_zero(tx.get("gas_price"))
    else:
        transaction["gasPrice"] = web3.eth.gas_price
    return transaction


def sign_transaction(
    tx: dict[str, Any],
    *,
    amount_usdt: float | int | str | None = None,
    action: str = "sign_live_transaction",
) -> dict[str, Any]:
    guard = assert_onchain_live_allowed(action, amount_usdt=amount_usdt, emit_log=False)
    if not guard.get("allowed"):
        return {"ok": False, "reason": guard.get("reason"), "live_guard": guard}
    wallet_guard = check_wallet_environment(emit_log=False)
    if not wallet_guard.get("wallet_signing_enabled"):
        return {"ok": False, "reason": "wallet_signing_not_enabled", "wallet_guard": wallet_guard}
    if not wallet_guard.get("wallet_address_configured"):
        return {"ok": False, "reason": "missing_wallet", "wallet_guard": wallet_guard}
    if not wallet_guard.get("private_key_configured"):
        return {"ok": False, "reason": "missing_private_key", "wallet_guard": wallet_guard}

    wallet = load_wallet_from_env()
    wallet_address = wallet.get("wallet_address")
    private_key = wallet.get("private_key")
    web3, rpc_error = _web3_for_tx(tx)
    if rpc_error:
        return {"ok": False, "reason": rpc_error, "wallet_guard": wallet_guard}
    try:
        transaction = _build_evm_transaction(tx, str(wallet_address), web3)
        signed = web3.eth.account.sign_transaction(transaction, private_key=str(private_key))
    except Exception as exc:
        return {"ok": False, "reason": "wallet_signing_failed", "message": str(exc), "wallet_guard": wallet_guard}

    raw_transaction = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
    tx_hash = getattr(signed, "hash", None)
    return {
        "ok": True,
        "reason": "ok",
        "signed_tx": raw_transaction.hex() if hasattr(raw_transaction, "hex") else raw_transaction,
        "tx_hash_preview": tx_hash.hex() if hasattr(tx_hash, "hex") else None,
        "wallet_guard": wallet_guard,
        "signed_at": datetime.now(timezone.utc).isoformat(),
    }


def broadcast_transaction(
    signed_tx: str,
    *,
    chain_id: str | int | None = None,
    chain_name: str | None = None,
) -> dict[str, Any]:
    settings = load_onchain_settings_config()
    if not settings.live_broadcast_enabled:
        return {"ok": False, "reason": "broadcast_not_enabled"}
    web3, rpc_error = _web3_for_tx({"chain_id": chain_id, "chain_name": chain_name})
    if rpc_error:
        return {"ok": False, "reason": rpc_error}
    try:
        tx_hash = web3.eth.send_raw_transaction(signed_tx)
    except Exception as exc:
        return {"ok": False, "reason": "broadcast_failed", "message": str(exc)}
    result = {"ok": True, "reason": "ok", "tx_hash": tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)}
    print(f"[ONCHAIN_BROADCAST] {json.dumps({'ok': result['ok'], 'tx_hash': result['tx_hash']}, sort_keys=True)}")
    return result
