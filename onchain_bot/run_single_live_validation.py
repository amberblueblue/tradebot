from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onchain_bot.config_loader import load_onchain_settings_config  # noqa: E402
from onchain_bot.live_executor import execute_live_swap  # noqa: E402
from onchain_bot.live_guard import assert_onchain_live_allowed  # noqa: E402


EXPLORER_BASE_BY_CHAIN_ID = {
    "1": "https://etherscan.io/tx/",
    "8453": "https://basescan.org/tx/",
    "42161": "https://arbiscan.io/tx/",
}


def _parse_amount(value: str | int | float | Decimal) -> Decimal:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("amount must be a number greater than 0") from exc
    if amount <= 0:
        raise ValueError("amount must be greater than 0")
    return amount


def explorer_link(chain_id: str | int | None, tx_hash: str | None) -> str | None:
    if not tx_hash:
        return None
    base_url = EXPLORER_BASE_BY_CHAIN_ID.get(str(chain_id or ""))
    if not base_url:
        return None
    return f"{base_url}{tx_hash}"


def run_single_live_validation(symbol: str, direction: str, amount_usdt: str | int | float | Decimal = "5") -> dict[str, Any]:
    settings = load_onchain_settings_config()
    normalized_symbol = symbol.strip().upper()
    normalized_direction = direction.strip().lower()
    if normalized_direction not in {"buy", "sell"}:
        raise ValueError("direction must be buy or sell")
    amount = _parse_amount(amount_usdt)
    if not settings.live_validation_mode:
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "direction": normalized_direction,
            "amount": float(amount),
            "reason": "validation_mode_disabled",
            "status": "blocked",
        }
    if amount > Decimal(str(settings.live_validation_max_order_usdt)):
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "direction": normalized_direction,
            "amount": float(amount),
            "reason": "validation_amount_exceeds_limit",
            "validation_max_order_usdt": settings.live_validation_max_order_usdt,
            "status": "blocked",
        }

    guard = assert_onchain_live_allowed(
        f"single_live_validation_{normalized_direction}",
        amount_usdt=amount,
        validation_required=True,
    )
    if not guard.get("allowed"):
        return {
            "ok": False,
            "symbol": normalized_symbol,
            "direction": normalized_direction,
            "amount": float(amount),
            "reason": guard.get("reason"),
            "status": "blocked",
            "live_guard": guard,
            "single_run_only": True,
            "unattended_trading": False,
        }

    result = execute_live_swap(
        normalized_symbol,
        normalized_direction,
        amount,
        validation_required=True,
    )
    tx_hash = result.get("tx_hash") or result.get("approve_tx_hash")
    swap_transaction = result.get("swap_transaction") if isinstance(result.get("swap_transaction"), dict) else {}
    approve_transaction = result.get("approve_transaction") if isinstance(result.get("approve_transaction"), dict) else {}
    chain_id = swap_transaction.get("chain_id") or approve_transaction.get("chain_id")
    parsed_quote = result.get("parsed_quote") if isinstance(result.get("parsed_quote"), dict) else {}
    return {
        **result,
        "mode": "single_live_validation",
        "single_run_only": True,
        "unattended_trading": False,
        "amount": float(amount),
        "status": result.get("reason") or ("submitted" if result.get("ok") else "blocked"),
        "gas": parsed_quote.get("estimated_gas_raw"),
        "explorer_link": explorer_link(chain_id, tx_hash),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one guarded Onchain live validation trade.")
    parser.add_argument("symbol")
    parser.add_argument("--direction", choices=("buy", "sell"), default="buy")
    parser.add_argument("--amount-usdt", default="5")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    payload = run_single_live_validation(args.symbol, args.direction, args.amount_usdt)
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
