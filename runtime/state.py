from __future__ import annotations

import os
from dataclasses import dataclass

from config.loader import ExecutionRuntimeConfig
from execution.broker import Broker
from execution.paper_broker import PaperBroker
from runtime.bot_state import ERROR, PAUSED, RUNNING, STOPPED
from runtime.state_store import RuntimeStore


SUPPORTED_MODES = {"backtest", "paper", "live"}


@dataclass(frozen=True)
class RuntimeState:
    mode: str
    broker_name: str
    is_live_enabled: bool


@dataclass(frozen=True)
class LiveGateStatus:
    mode: str
    allow_live_trading: bool
    confirm_env_ok: bool
    gate_passed: bool
    message: str


def get_live_gate_status(config: ExecutionRuntimeConfig) -> LiveGateStatus:
    confirm_env_ok = os.environ.get("TRADEBOT_CONFIRM_LIVE") == "YES"
    if config.mode != "live":
        return LiveGateStatus(
            mode=config.mode,
            allow_live_trading=config.allow_live_trading,
            confirm_env_ok=confirm_env_ok,
            gate_passed=False,
            message="paper mode only; live gate inactive",
        )

    gate_passed = config.allow_live_trading and confirm_env_ok
    if gate_passed:
        message = "live gate passed but live broker not implemented"
    elif not config.allow_live_trading:
        message = "live mode blocked by safety.allow_live_trading=false"
    else:
        message = "live mode blocked because TRADEBOT_CONFIRM_LIVE is not YES"

    return LiveGateStatus(
        mode=config.mode,
        allow_live_trading=config.allow_live_trading,
        confirm_env_ok=confirm_env_ok,
        gate_passed=gate_passed,
        message=message,
    )


def build_runtime_state(config: ExecutionRuntimeConfig) -> RuntimeState:
    if config.mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported app mode: {config.mode}")

    broker_name = "none"
    if config.mode == "paper":
        broker_name = "paper"
    elif config.mode == "live":
        broker_name = "live"

    return RuntimeState(
        mode=config.mode,
        broker_name=broker_name,
        is_live_enabled=config.live_enabled,
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
        raise RuntimeError(live_gate.message)
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
