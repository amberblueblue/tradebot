from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from onchain_bot.config_loader import load_onchain_settings_config


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = PROJECT_ROOT / ".env"
ONCHAIN_WALLET_ADDRESS_ENV = "ONCHAIN_WALLET_ADDRESS"
ONCHAIN_PRIVATE_KEY_ENV = "ONCHAIN_PRIVATE_KEY"


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


def check_wallet_environment(*, emit_log: bool = True) -> dict[str, Any]:
    settings = load_onchain_settings_config()
    wallet_address_configured = bool(os.environ.get(ONCHAIN_WALLET_ADDRESS_ENV))
    private_key_configured = bool(os.environ.get(ONCHAIN_PRIVATE_KEY_ENV))
    wallet_signing_enabled = bool(settings.live_wallet_signing_enabled)
    broadcast_enabled = bool(settings.live_broadcast_enabled)

    reason = "ok"
    ready_for_signing = True
    if not wallet_signing_enabled:
        reason = "wallet_signing_disabled"
        ready_for_signing = False
    elif settings.live_require_wallet_env and not wallet_address_configured:
        reason = "wallet_address_missing"
        ready_for_signing = False
    elif settings.live_require_wallet_env and not private_key_configured:
        reason = "wallet_private_key_missing"
        ready_for_signing = False

    result = {
        "wallet_address_configured": wallet_address_configured,
        "private_key_configured": private_key_configured,
        "wallet_signing_enabled": wallet_signing_enabled,
        "broadcast_enabled": broadcast_enabled,
        "ready_for_signing": ready_for_signing,
        "reason": reason,
        "wallet_address_env": ONCHAIN_WALLET_ADDRESS_ENV,
        "private_key_env": ONCHAIN_PRIVATE_KEY_ENV,
    }
    if emit_log:
        print(f"[ONCHAIN_WALLET_GUARD] {json.dumps(result, ensure_ascii=False, sort_keys=True)}")
    return result
