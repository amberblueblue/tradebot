from __future__ import annotations

from dataclasses import dataclass

from config.loader import ExecutionRuntimeConfig
from execution.broker import Broker
from execution.live_broker import LiveBroker
from execution.paper_broker import PaperBroker


SUPPORTED_MODES = {"backtest", "paper", "live"}


@dataclass(frozen=True)
class RuntimeState:
    mode: str
    broker_name: str
    is_live_enabled: bool


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
        )
    if state.mode == "live":
        if not state.is_live_enabled:
            raise RuntimeError("Live mode is configured but live.enabled is false")
        return LiveBroker()
    raise RuntimeError(f"Mode '{state.mode}' does not support broker execution")
