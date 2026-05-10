from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from futures_bot.config_loader import load_yaml_mapping
from onchain_bot.config_loader import load_onchain_settings_config


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_SAFETY_PATH = PROJECT_ROOT / "config" / "runtime_safety.yaml"
DOTENV_PATH = PROJECT_ROOT / ".env"
ONCHAIN_CONFIRM_LIVE_ENV = "ONCHAIN_CONFIRM_LIVE"
ONCHAIN_CONFIRM_LIVE_VALUE = "YES"


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


def _runtime_safety() -> dict[str, Any]:
    try:
        payload = load_yaml_mapping(RUNTIME_SAFETY_PATH)
    except Exception:
        payload = {}
    onchain = payload.get("onchain")
    return {
        "global_kill_switch": bool(payload.get("global_kill_switch", False)),
        "onchain_kill_switch": bool(onchain.get("kill_switch", False)) if isinstance(onchain, dict) else False,
        "onchain_trading_enabled": bool(onchain.get("trading_enabled", False)) if isinstance(onchain, dict) else False,
    }


def _amount_decimal(amount: float | int | str | Decimal | None) -> Decimal:
    try:
        parsed = Decimal(str(amount if amount is not None else "0"))
    except InvalidOperation as exc:
        raise ValueError("amount must be a number greater than 0") from exc
    if parsed <= 0:
        raise ValueError("amount must be greater than 0")
    return parsed


def assert_onchain_live_allowed(
    action: str | None = None,
    *,
    amount_usdt: float | int | str | Decimal | None = None,
    emit_log: bool = True,
) -> dict[str, Any]:
    settings = load_onchain_settings_config()
    safety = _runtime_safety()
    amount = _amount_decimal(amount_usdt or settings.live_default_order_amount_usdt)
    max_amount = Decimal(str(settings.risk_max_live_order_usdt))

    reason = "ok"
    allowed = True
    if safety["global_kill_switch"]:
        reason = "global_kill_switch_enabled"
        allowed = False
    elif safety["onchain_kill_switch"]:
        reason = "onchain_kill_switch_enabled"
        allowed = False
    elif not safety["onchain_trading_enabled"]:
        reason = "onchain_trading_disabled"
        allowed = False
    elif not settings.live_auto_live_enabled:
        reason = "onchain_auto_live_disabled"
        allowed = False
    elif settings.live_require_manual_confirm_env and os.environ.get(ONCHAIN_CONFIRM_LIVE_ENV) != ONCHAIN_CONFIRM_LIVE_VALUE:
        reason = "onchain_live_not_confirmed"
        allowed = False
    elif amount > max_amount:
        reason = "live_order_amount_exceeds_max"
        allowed = False

    result = {
        "allowed": allowed,
        "reason": reason,
        "action": action,
        "amount_usdt": float(amount),
        "max_live_order_usdt": float(max_amount),
        "auto_live_supported": True,
        "auto_live_enabled": bool(settings.live_auto_live_enabled),
        "require_manual_confirm_env": bool(settings.live_require_manual_confirm_env),
        "confirm_env_name": ONCHAIN_CONFIRM_LIVE_ENV,
        "confirm_env_present": os.environ.get(ONCHAIN_CONFIRM_LIVE_ENV) == ONCHAIN_CONFIRM_LIVE_VALUE,
        **safety,
    }
    if emit_log:
        print(f"[ONCHAIN_LIVE_GUARD] {json.dumps(result, ensure_ascii=False, sort_keys=True)}")
    return result
