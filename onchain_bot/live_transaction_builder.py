from __future__ import annotations

import json
from typing import Any


TX_FIELD_ALIASES = {
    "to": ("to", "toAddress", "contractAddress"),
    "data": ("data", "callData", "input"),
    "value": ("value", "amount"),
    "gas": ("gas", "gasLimit", "gasLimitRaw"),
    "gas_price": ("gasPrice", "gas_price", "maxFeePerGas"),
}
SPENDER_KEYS = (
    "dexContractAddress",
    "spender",
    "spenderAddress",
    "approveAddress",
    "approveContractAddress",
    "router",
    "routerAddress",
)


def _first_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        for item in value:
            found = _first_mapping(item)
            if found is not None:
                return found
    return None


def _extract_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "tx_preview" in payload:
        payload = payload.get("tx_preview")
    if isinstance(payload, dict) and isinstance(payload.get("data"), list) and payload["data"]:
        return payload["data"][0]
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def _find_first_key(data: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value.startswith("0x"):
                return value
        for value in data.values():
            found = _find_first_key(value, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_first_key(item, keys)
            if found is not None:
                return found
    return None


def _find_named_tx(data: Any, names: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    for name in names:
        if name in data:
            found = _first_mapping(data.get(name))
            if found is not None:
                return found
    for value in data.values():
        if isinstance(value, dict):
            found = _find_named_tx(value, names)
            if found is not None:
                return found
        elif isinstance(value, list):
            for item in value:
                found = _find_named_tx(item, names)
                if found is not None:
                    return found
    return None


def _looks_like_tx(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    if any(key in data for key in ("to", "toAddress")) and any(key in data for key in ("data", "callData", "input")):
        return data
    for key in ("tx", "transaction", "swapTransaction"):
        found = _first_mapping(data.get(key))
        if found is not None:
            candidate = _looks_like_tx(found)
            if candidate is not None:
                return candidate
    return None


def _pick(raw_tx: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if key in raw_tx and raw_tx[key] not in (None, ""):
            return raw_tx[key]
    return None


def normalize_transaction_object(
    raw_tx: dict[str, Any] | None,
    *,
    chain_id: str,
    direction: str,
    symbol: str,
) -> dict[str, Any] | None:
    if raw_tx is None:
        return None
    return {
        "chain_id": str(chain_id),
        "to": _pick(raw_tx, TX_FIELD_ALIASES["to"]),
        "data": _pick(raw_tx, TX_FIELD_ALIASES["data"]),
        "value": str(_pick(raw_tx, TX_FIELD_ALIASES["value"]) or "0"),
        "gas": _pick(raw_tx, TX_FIELD_ALIASES["gas"]),
        "gas_price": _pick(raw_tx, TX_FIELD_ALIASES["gas_price"]),
        "nonce": None,
        "direction": direction,
        "symbol": symbol,
    }


def build_unsigned_transactions(
    tx_preview: dict[str, Any] | None,
    *,
    chain_id: str,
    direction: str,
    symbol: str,
) -> dict[str, Any]:
    if not isinstance(tx_preview, dict) or not tx_preview.get("ok"):
        return {
            "ok": False,
            "approve_transaction": None,
            "swap_transaction": None,
            "failures": ["tx_preview_not_available"],
            "warnings": [],
        }

    data = _extract_data(tx_preview)
    approve_raw = _find_named_tx(data, ("approveTransaction", "approveTx", "approve_tx"))
    swap_raw = _find_named_tx(data, ("swapTransaction", "swapTx", "swap_tx", "tx", "transaction")) or _looks_like_tx(data)
    approve_transaction = normalize_transaction_object(
        approve_raw,
        chain_id=chain_id,
        direction=direction,
        symbol=symbol,
    )
    swap_transaction = normalize_transaction_object(
        swap_raw,
        chain_id=chain_id,
        direction=direction,
        symbol=symbol,
    )

    failures: list[str] = []
    if swap_transaction is None or not swap_transaction.get("to") or not swap_transaction.get("data"):
        failures.append("swap_tx_data_missing")
    warnings: list[str] = []
    if approve_transaction is None:
        warnings.append("approve_tx_not_required_or_missing")

    result = {
        "ok": not failures,
        "approve_transaction": approve_transaction,
        "swap_transaction": swap_transaction,
        "failures": failures,
        "warnings": warnings,
    }
    print(f"[ONCHAIN_UNSIGNED_TX] {json.dumps({**result, 'raw_tx_omitted': True}, ensure_ascii=False, sort_keys=True)}")
    return result


def extract_spender_address(
    tx_preview: dict[str, Any] | None,
    *,
    approve_transaction: dict[str, Any] | None = None,
    swap_transaction: dict[str, Any] | None = None,
) -> str | None:
    data = _extract_data(tx_preview)
    explicit_spender = _find_first_key(data, SPENDER_KEYS)
    if explicit_spender:
        return explicit_spender
    if isinstance(swap_transaction, dict) and isinstance(swap_transaction.get("to"), str):
        return swap_transaction["to"]
    if isinstance(approve_transaction, dict) and isinstance(approve_transaction.get("to"), str):
        return approve_transaction["to"]
    return None
