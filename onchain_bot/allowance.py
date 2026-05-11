from __future__ import annotations

from typing import Any

from onchain_bot.wallet_signer import get_rpc_url


ERC20_ALLOWANCE_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    }
]

NATIVE_TOKEN_ADDRESSES = {
    "",
    "0x0000000000000000000000000000000000000000",
    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
}


def _is_native_token(token_address: str | None) -> bool:
    return str(token_address or "").strip().lower() in NATIVE_TOKEN_ADDRESSES


def get_erc20_allowance(
    chain_id: str,
    token_address: str,
    owner_address: str,
    spender_address: str,
) -> dict[str, Any]:
    normalized_token = str(token_address or "").strip()
    normalized_owner = str(owner_address or "").strip()
    normalized_spender = str(spender_address or "").strip()
    if _is_native_token(normalized_token):
        return {
            "ok": True,
            "chain_id": str(chain_id),
            "token_address": normalized_token,
            "owner_address_configured": bool(normalized_owner),
            "spender": normalized_spender,
            "allowance": None,
            "native_token": True,
            "error": None,
        }
    if not normalized_owner.startswith("0x"):
        return {"ok": False, "error": "missing_owner_address", "allowance": None}
    if not normalized_spender.startswith("0x"):
        return {"ok": False, "error": "missing_spender_address", "allowance": None}
    rpc_url = get_rpc_url(chain_id)
    if not rpc_url:
        return {"ok": False, "error": "missing_rpc_url", "allowance": None}
    try:
        from web3 import Web3
    except ModuleNotFoundError:
        return {"ok": False, "error": "web3_not_installed", "allowance": None}
    try:
        web3 = Web3(Web3.HTTPProvider(rpc_url))
        token = web3.eth.contract(
            address=web3.to_checksum_address(normalized_token),
            abi=ERC20_ALLOWANCE_ABI,
        )
        allowance = token.functions.allowance(
            web3.to_checksum_address(normalized_owner),
            web3.to_checksum_address(normalized_spender),
        ).call()
    except Exception as exc:
        return {
            "ok": False,
            "chain_id": str(chain_id),
            "token_address": normalized_token,
            "spender": normalized_spender,
            "allowance": None,
            "error": "allowance_query_failed",
            "message": str(exc),
        }
    return {
        "ok": True,
        "chain_id": str(chain_id),
        "token_address": normalized_token,
        "owner_address_configured": True,
        "spender": normalized_spender,
        "allowance": str(int(allowance)),
        "native_token": False,
        "error": None,
    }


def allowance_sufficient(current_allowance: int | str | None, required_amount: int | str | None) -> bool:
    if current_allowance is None:
        return False
    try:
        return int(current_allowance) >= int(required_amount or 0)
    except (TypeError, ValueError):
        return False


def build_allowance_result(
    *,
    chain_id: str,
    token_address: str,
    owner_address: str,
    spender_address: str,
    required_amount: int | str,
    token_symbol: str | None = None,
) -> dict[str, Any]:
    allowance = get_erc20_allowance(chain_id, token_address, owner_address, spender_address)
    native_token = bool(allowance.get("native_token"))
    sufficient = True if native_token else allowance_sufficient(allowance.get("allowance"), required_amount)
    return {
        "ok": bool(allowance.get("ok")),
        "chain_id": str(chain_id),
        "token_symbol": token_symbol,
        "token_address": token_address,
        "spender": spender_address,
        "required_amount": str(required_amount),
        "current_allowance": allowance.get("allowance"),
        "sufficient": sufficient,
        "native_token": native_token,
        "allowance": allowance,
        "error": allowance.get("error"),
        "message": allowance.get("message"),
    }
