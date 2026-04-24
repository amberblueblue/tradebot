from __future__ import annotations

import time

import feature_engine
from config.loader import load_execution_runtime, load_project_config
from exchange.binance_client import BinanceClient
from execution.trader import TraderEngine
from observability.event_logger import LogRouter
from runtime.sync import startup_sync
from runtime.state import RuntimeStore, build_runtime_state, create_broker, get_live_gate_status
from storage.db import initialize_database
from strategy.config import StrategyConfig


def ensure_runtime_mode_allowed(execution_config) -> None:
    runtime_state = build_runtime_state(execution_config)
    if runtime_state.mode == "paper":
        return

    live_gate = get_live_gate_status(execution_config)
    raise RuntimeError(live_gate.message)


def main() -> None:
    initialize_database()
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    runtime_state = build_runtime_state(execution_config)
    ensure_runtime_mode_allowed(execution_config)

    broker = create_broker(execution_config)
    runtime_store = RuntimeStore(
        execution_config.runtime_state_file,
        status_path=execution_config.status_file,
        initial_status=execution_config.robot_initial_status,
        mode=execution_config.mode,
        broker_name=runtime_state.broker_name,
    )
    client = BinanceClient(
        base_url=execution_config.exchange.base_url,
        api_key=execution_config.exchange.api_key,
        api_secret=execution_config.exchange.api_secret,
        recv_window=execution_config.exchange.recv_window,
    )
    logger = LogRouter(
        system_log=execution_config.system_log_file,
        trade_log=execution_config.trade_log_file,
        error_log=execution_config.error_log_file,
        mode=execution_config.mode,
    )
    startup_sync(
        broker=broker,
        execution_config=execution_config,
        runtime_store=runtime_store,
        logger=logger,
    )
    trader = TraderEngine(
        broker=broker,
        market_client=client,
        runtime_store=runtime_store,
        strategy_config=StrategyConfig.from_settings(settings),
        feature_config=feature_engine.FeatureConfig.from_dict(settings.get("feature_engine", {})),
        execution_config=execution_config,
    )

    print(
        f"Starting bot in {runtime_state.mode} mode for symbols: {', '.join(execution_config.enabled_symbols)}"
    )
    try:
        while True:
            try:
                trader.run_once()
            except Exception as exc:
                trader._record_error("SYSTEM", exc)
            time.sleep(trader.execution_config.polling_interval_seconds)
    except KeyboardInterrupt:
        runtime_store.set_robot_status("stopped")
        logger.log_system(symbol="-", action="shutdown", reason="keyboard_interrupt")
        print("Bot stopped by user.")


if __name__ == "__main__":
    main()
