from __future__ import annotations

import argparse
import json
from dataclasses import replace
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from config.loader import SymbolTradingConfig
from config.loader import load_execution_runtime, load_project_config
from exchange.binance_client import BinanceClient
from exchange.rules import parse_symbol_rules
from execution.order_validator import validate_entry_order
from runtime.state import RuntimeStore, build_runtime_state


def _default_symbol_state() -> dict:
    return {
        "strategy_state": "IDLE",
        "entry_price": None,
        "entry_bar_index": None,
        "partial1_done": False,
        "partial2_done": False,
        "max_unrealized_return": 0.0,
        "cooldown_remaining": 0,
        "last_bar_timestamp": None,
        "last_action_bar_timestamp": None,
        "last_signal": None,
        "realized_pnl": 0.0,
        "paused_by_loss": False,
    }


def reset_paper_runtime() -> None:
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    if execution_config.mode != "paper":
        raise RuntimeError("reset is only allowed when app.mode=paper")

    runtime_state = build_runtime_state(execution_config)
    store = RuntimeStore(
        execution_config.runtime_state_file,
        status_path=execution_config.status_file,
        initial_status="running",
        mode=execution_config.mode,
        broker_name=runtime_state.broker_name,
    )
    store.state.robot_status = "running"
    store.state.conservative_mode = False
    store.state.consecutive_errors = 0
    store.state.last_error = None
    store.state.symbols = {
        symbol: _default_symbol_state()
        for symbol in execution_config.enabled_symbols
    }
    store.save()
    print("paper runtime reset: robot_status=running conservative_mode=false consecutive_errors=0")


def reset_signal_guard() -> None:
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    if execution_config.mode != "paper":
        raise RuntimeError("signal guard reset is only allowed when app.mode=paper")

    runtime_state = build_runtime_state(execution_config)
    store = RuntimeStore(
        execution_config.runtime_state_file,
        status_path=execution_config.status_file,
        initial_status="running",
        mode=execution_config.mode,
        broker_name=runtime_state.broker_name,
    )
    store.state.robot_status = "running"
    store.state.conservative_mode = False
    store.state.consecutive_errors = 0
    store.state.last_error = None
    for symbol_state in store.state.symbols.values():
        symbol_state["last_bar_timestamp"] = None
        symbol_state["last_action_bar_timestamp"] = None
        symbol_state["last_signal"] = None
    store.save()
    print("signal guard reset: robot_status=running conservative_mode=false duplicate guards cleared")


def _round_down_to_step(value: float, step: float) -> float:
    decimal_value = Decimal(str(value))
    decimal_step = Decimal(str(step))
    if decimal_step <= 0:
        return float(decimal_value)
    return float((decimal_value / decimal_step).to_integral_value(rounding=ROUND_DOWN) * decimal_step)


def _dry_run_symbol_config(
    symbol: str,
    amount: float,
    existing_config: SymbolTradingConfig | None,
) -> SymbolTradingConfig:
    if existing_config is not None:
        return replace(existing_config, order_amount=amount)
    return SymbolTradingConfig(
        symbol=symbol,
        enabled=True,
        trend_timeframe="4h",
        signal_timeframe="15m",
        order_amount=amount,
        max_loss_amount=20.0,
        paused_by_loss=False,
    )


def _validate_order_dry_run(symbol: str, side: str, amount: float) -> dict:
    symbol = symbol.upper()
    side = side.lower()
    if amount <= 0:
        raise ValueError("--amount must be greater than 0")
    if side != "buy":
        return {
            "ok": False,
            "reason": "unsupported_side",
            "symbol": symbol,
            "side": side,
            "supported_sides": ["buy"],
            "dry_run": True,
        }

    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    client = BinanceClient(
        base_url=execution_config.exchange.base_url,
        timeout=execution_config.exchange.request_timeout_seconds,
        error_log_file=execution_config.error_log_file,
    )

    ticker = client.get_ticker_price(symbol)
    raw_price = float(ticker["price"])
    symbol_info = client.get_symbol_info(symbol)
    rules = parse_symbol_rules(symbol, symbol_info)

    normalized_price = _round_down_to_step(raw_price, rules.tick_size)
    raw_quantity = amount / raw_price
    normalized_quantity = _round_down_to_step(raw_quantity, rules.step_size)
    symbol_config = _dry_run_symbol_config(
        symbol,
        amount,
        execution_config.symbol_configs.get(symbol),
    )

    validation = validate_entry_order(
        symbol_config=symbol_config,
        quantity=normalized_quantity,
        price=normalized_price,
        realized_pnl=0.0,
        current_position_count=0,
        max_positions=execution_config.max_positions,
        bot_status="running",
        rules=rules,
    )
    return {
        "ok": validation.ok,
        "reason": validation.reason,
        "symbol": symbol,
        "side": side,
        "amount": amount,
        "ticker_price": raw_price,
        "raw_quantity": raw_quantity,
        "normalized_price": validation.normalized_price,
        "normalized_quantity": validation.normalized_quantity,
        "notional": validation.notional,
        "min_notional": validation.min_notional,
        "rules": {
            "tick_size": rules.tick_size,
            "step_size": rules.step_size,
            "min_qty": rules.min_qty,
            "max_qty": rules.max_qty,
            "min_notional": rules.effective_min_notional,
            "notional_max": rules.notional_max,
        },
        "dry_run": True,
        "broker_called": False,
        "trade_written": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Show or reset TraderBot runtime status.")
    parser.add_argument(
        "--reset-paper",
        action="store_true",
        help="Reset paper runtime status out of error/conservative mode.",
    )
    parser.add_argument(
        "--reset-signal-guard",
        action="store_true",
        help="Clear duplicate bar/signal guard state without deleting trading history.",
    )
    parser.add_argument(
        "--validate-order",
        metavar="SYMBOL",
        help="Dry-run validate a buy order using Binance public ticker and exchangeInfo rules.",
    )
    parser.add_argument(
        "--side",
        choices=("buy", "sell"),
        help="Order side for --validate-order. Only buy is currently supported.",
    )
    parser.add_argument(
        "--amount",
        type=float,
        help="Quote amount in USDT for --validate-order.",
    )
    args = parser.parse_args()
    if args.reset_paper:
        reset_paper_runtime()
        return
    if args.reset_signal_guard:
        reset_signal_guard()
        return
    if args.validate_order:
        if args.side is None or args.amount is None:
            parser.error("--validate-order requires --side and --amount")
        payload = _validate_order_dry_run(args.validate_order, args.side, args.amount)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    status_path = Path(execution_config.status_file)
    if not status_path.exists():
        print(f"Status file not found: {status_path}")
        return

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
