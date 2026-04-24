from __future__ import annotations

from config.loader import load_execution_runtime, load_project_config
from execution.paper_broker import PaperBroker
from runtime.state import build_runtime_state


def main() -> None:
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    runtime_state = build_runtime_state(execution_config)
    if runtime_state.mode != "paper":
        raise RuntimeError("Please set app.mode=paper before running the paper demo")

    broker = PaperBroker(
        initial_cash=execution_config.paper_initial_cash,
        state_file=execution_config.paper_state_file,
        trade_log_file=execution_config.paper_trade_log_file,
    )
    symbol = execution_config.symbol_list[0] if execution_config.symbol_list else "BTCUSDT"

    broker.set_market_price(symbol, 100.0)
    buy_result = broker.place_market_buy(symbol, 10.0)

    broker.set_market_price(symbol, 105.0)
    sell_result = broker.place_market_sell(symbol, 4.0)

    print(f"Mode: {runtime_state.mode}")
    print(f"Buy result: {buy_result}")
    print(f"Sell result: {sell_result}")
    print(f"Cash balance: {broker.get_cash_balance():.2f}")
    print(f"Positions: {broker.get_positions()}")


if __name__ == "__main__":
    main()
