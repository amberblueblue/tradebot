from __future__ import annotations

from dataclasses import asdict

from config.loader import ExecutionRuntimeConfig
from execution.broker import Broker
from observability.event_logger import LogRouter
from runtime.bot_state import PAUSED, SyncSnapshot


def startup_sync(
    *,
    broker: Broker,
    execution_config: ExecutionRuntimeConfig,
    runtime_store,
    logger: LogRouter,
) -> SyncSnapshot:
    warnings: list[str] = []

    try:
        cash_balance = broker.get_cash_balance()
        positions = [asdict(position) for position in broker.get_positions()]
        open_orders = broker.get_open_orders()
    except Exception as exc:
        cash_balance = 0.0
        positions = []
        open_orders = []
        warnings.append(f"startup_sync_failed: {exc}")

    if not execution_config.enabled_symbols:
        warnings.append("no_enabled_symbols")

    if execution_config.mode == "live" and not execution_config.live_enabled:
        warnings.append("live_mode_not_enabled")

    snapshot = SyncSnapshot(
        cash_balance=float(cash_balance),
        positions=positions,
        open_orders=open_orders,
        enabled_symbols=list(execution_config.enabled_symbols),
        warnings=warnings,
    )
    runtime_store.set_sync_snapshot(snapshot)

    for position in positions:
        runtime_store.get_symbol_state(position["symbol"])

    if warnings:
        runtime_store.set_conservative_mode(True)
        if runtime_store.get_robot_status() == "running":
            runtime_store.set_robot_status(PAUSED)
        for warning in warnings:
            logger.log_system(symbol="-", action="startup_warning", reason=warning, snapshot=asdict(snapshot))
    else:
        runtime_store.set_conservative_mode(False)
        logger.log_system(symbol="-", action="startup_sync", reason="startup_sync_ok", snapshot=asdict(snapshot))

    return snapshot
