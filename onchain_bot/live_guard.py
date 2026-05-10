from __future__ import annotations

from typing import Any

from runtime.safety import load_runtime_safety_config


AUTO_LIVE_SUPPORTED = False
AUTO_LIVE_REASON = "onchain_auto_live_not_supported"


def assert_onchain_live_allowed(action: str | None = None) -> dict[str, Any]:
    safety = load_runtime_safety_config()
    return {
        "auto_live_supported": AUTO_LIVE_SUPPORTED,
        "auto_live_enabled": False,
        "configured_auto_live_enabled": bool(getattr(safety, "onchain_auto_live_enabled", False)),
        "allowed": False,
        "reason": AUTO_LIVE_REASON,
        "action": action,
    }
