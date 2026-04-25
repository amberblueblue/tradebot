from __future__ import annotations

import os
from dataclasses import dataclass

from config.loader import ExecutionRuntimeConfig
from execution.broker import Broker
from execution.live_broker import LiveBroker
from execution.paper_broker import PaperBroker
from runtime.bot_state import ERROR, PAUSED, RUNNING, STOPPED
from runtime.state_store import RuntimeStore


SUPPORTED_MODES = {"backtest", "paper", "live"}
REAL_EXECUTE_ENV_VAR = "TRADEBOT_EXECUTE_REAL"
REAL_EXECUTE_ENV_VALUE = "YES"


@dataclass(frozen=True)
class RuntimeState:
    mode: str
    broker_name: str
    is_live_enabled: bool
    is_real_trading_enabled: bool
    uses_real_order_api: bool


@dataclass(frozen=True)
class LiveGateStatus:
    mode: str
    allow_live_trading: bool
    live_execute_enabled: bool
    require_manual_confirm: bool
    confirm_env_ok: bool
    real_execute_env_ok: bool
    gate_passed: bool
    real_trading_enabled: bool
    uses_real_order_api: bool
    broker_name: str
    message: str


def get_live_gate_status(config: ExecutionRuntimeConfig) -> LiveGateStatus:
    confirm_env_ok = os.environ.get("TRADEBOT_CONFIRM_LIVE") == "YES"
    real_execute_env_ok = os.environ.get(REAL_EXECUTE_ENV_VAR) == REAL_EXECUTE_ENV_VALUE
    if config.mode != "live":
        return LiveGateStatus(
            mode=config.mode,
            allow_live_trading=config.allow_live_trading,
            live_execute_enabled=config.live_execute_enabled,
            require_manual_confirm=config.require_manual_confirm,
            confirm_env_ok=confirm_env_ok,
            real_execute_env_ok=real_execute_env_ok,
            gate_passed=False,
            real_trading_enabled=False,
            uses_real_order_api=False,
            broker_name="paper",
            message="paper mode only; live gate inactive",
        )

    gate_passed = config.allow_live_trading and confirm_env_ok and config.live_execute_enabled
    real_trading_enabled = (
        gate_passed
        and config.require_manual_confirm
        and real_execute_env_ok
    )
    if gate_passed:
        message = "live enabled; broker remains simulation-only"
    elif not config.allow_live_trading:
        message = "live mode blocked by safety.allow_live_trading=false"
    elif not confirm_env_ok:
        message = "live mode blocked because TRADEBOT_CONFIRM_LIVE is not YES"
    else:
        message = "live mode blocked by safety.live_execute_enabled=false"

    return LiveGateStatus(
        mode=config.mode,
        allow_live_trading=config.allow_live_trading,
        live_execute_enabled=config.live_execute_enabled,
        require_manual_confirm=config.require_manual_confirm,
        confirm_env_ok=confirm_env_ok,
        real_execute_env_ok=real_execute_env_ok,
        gate_passed=gate_passed,
        real_trading_enabled=real_trading_enabled,
        uses_real_order_api=False,
        broker_name="live_enabled" if real_trading_enabled else "live_simulation" if gate_passed else "paper",
        message=message,
    )


def build_runtime_state(config: ExecutionRuntimeConfig) -> RuntimeState:
    if config.mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported app mode: {config.mode}")

    live_gate = get_live_gate_status(config)
    broker_name = "none"
    if config.mode == "paper":
        broker_name = "paper"
    elif config.mode == "live":
        broker_name = live_gate.broker_name

    return RuntimeState(
        mode=config.mode,
        broker_name=broker_name,
        is_live_enabled=live_gate.gate_passed,
        is_real_trading_enabled=live_gate.real_trading_enabled,
        uses_real_order_api=live_gate.uses_real_order_api,
    )


def create_broker(config: ExecutionRuntimeConfig) -> Broker:
    state = build_runtime_state(config)
    if state.mode == "paper":
        return PaperBroker(
            initial_cash=config.paper_initial_cash,
            state_file=config.paper_state_file,
            trade_log_file=config.paper_trade_log_file,
            error_log_file=config.error_log_file,
            mode=config.mode,
        )
    if state.mode == "live":
        live_gate = get_live_gate_status(config)
        if not live_gate.gate_passed:
            return PaperBroker(
                initial_cash=config.paper_initial_cash,
                state_file=config.paper_state_file,
                trade_log_file=config.paper_trade_log_file,
                error_log_file=config.error_log_file,
                mode="paper",
            )
        return LiveBroker()
    raise RuntimeError(f"Mode '{state.mode}' does not support broker execution")


__all__ = [
    "ERROR",
    "PAUSED",
    "RUNNING",
    "STOPPED",
    "RuntimeState",
    "RuntimeStore",
    "build_runtime_state",
    "create_broker",
    "get_live_gate_status",
]
