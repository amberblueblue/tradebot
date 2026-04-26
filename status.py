from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from config.loader import SymbolTradingConfig
from config.loader import load_execution_runtime, load_project_config
from exchange.binance_client import BinanceClient
from exchange.rules import parse_symbol_rules
from execution.order_validator import validate_entry_order
from observability.event_logger import LogRouter
from runtime.state import RuntimeStore, build_runtime_state
from storage.repository import StorageRepository


LIVE_CONFIRM_ENV_VAR = "TRADEBOT_CONFIRM_LIVE"
EXECUTE_REAL_ENV_VAR = "TRADEBOT_EXECUTE_REAL"
FINAL_REAL_ORDER_ENV_VAR = "TRADEBOT_FINAL_REAL_ORDER"
REQUIRED_ENV_VALUE = "YES"
DEFAULT_MIN_NOTIONAL_USDT = 5.0


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
        max_single_order_usdt=execution_config.max_single_order_usdt,
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
        "order_amount": validation.order_amount,
        "max_single_order_usdt": validation.max_single_order_usdt,
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


def _usdt_free_balance(client: BinanceClient) -> tuple[float, str | None]:
    try:
        balances = client.get_account_balances()
    except Exception as exc:
        return 0.0, str(exc)

    for balance in balances:
        if str(balance.get("asset", "")).upper() == "USDT":
            try:
                return float(balance.get("free", 0) or 0), None
            except (TypeError, ValueError) as exc:
                return 0.0, f"Invalid USDT balance payload: {exc}"
    return 0.0, None


def _local_min_notional_reject_payload(symbol: str, side: str, amount: float) -> dict:
    return {
        "validator_ok": False,
        "validator_reason": "order_amount_below_min_notional",
        "exchange_test_order_ok": False,
        "broker_called": False,
        "real_order_sent": False,
        "symbol": symbol,
        "requested_symbol": symbol,
        "exchange_symbol": None,
        "side": side,
        "amount": amount,
        "order_amount": amount,
        "min_notional": DEFAULT_MIN_NOTIONAL_USDT,
        "exchange_test_order": None,
    }


def _exchange_test_order(symbol: str, side: str, amount: float) -> dict:
    symbol = symbol.upper()
    side = side.lower()
    if amount <= 0:
        raise ValueError("--amount must be greater than 0")
    if side not in {"buy", "sell"}:
        raise ValueError("--side must be buy or sell")
    if side == "buy" and amount < DEFAULT_MIN_NOTIONAL_USDT:
        return _local_min_notional_reject_payload(symbol, side, amount)

    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    exchange_symbol = symbol
    client = BinanceClient(
        base_url=execution_config.exchange.base_url,
        timeout=execution_config.exchange.request_timeout_seconds,
        error_log_file=execution_config.error_log_file,
        recv_window=execution_config.exchange.recv_window,
    )

    ticker = client.get_ticker_price(exchange_symbol)
    raw_price = float(ticker["price"])
    symbol_info = client.get_symbol_info(exchange_symbol)
    rules = parse_symbol_rules(exchange_symbol, symbol_info)

    normalized_price = _round_down_to_step(raw_price, rules.tick_size)
    if side == "buy":
        order_amount = amount
        raw_quantity = amount / raw_price
        normalized_quantity = _round_down_to_step(raw_quantity, rules.step_size)
    else:
        raw_quantity = amount
        normalized_quantity = _round_down_to_step(raw_quantity, rules.step_size)
        order_amount = float(Decimal(str(normalized_quantity)) * Decimal(str(normalized_price)))

    symbol_config = _dry_run_symbol_config(
        symbol,
        order_amount,
        execution_config.symbol_configs.get(symbol),
    )
    usdt_balance = None
    balance_error = None
    if side == "buy" and order_amount >= rules.effective_min_notional:
        usdt_balance, balance_error = _usdt_free_balance(client)

    validation = validate_entry_order(
        symbol_config=symbol_config,
        quantity=normalized_quantity,
        price=normalized_price,
        realized_pnl=0.0,
        current_position_count=0,
        max_positions=execution_config.max_positions,
        max_single_order_usdt=execution_config.max_single_order_usdt,
        bot_status="running",
        usdt_available_balance=usdt_balance,
        rules=rules,
    )

    payload = {
        "validator_ok": validation.ok,
        "validator_reason": validation.reason,
        "exchange_test_order_ok": False,
        "broker_called": False,
        "real_order_sent": False,
        "symbol": symbol,
        "requested_symbol": symbol,
        "exchange_symbol": exchange_symbol,
        "side": side,
        "amount": amount,
        "ticker_price": raw_price,
        "raw_quantity": raw_quantity,
        "normalized_price": validation.normalized_price,
        "normalized_quantity": validation.normalized_quantity,
        "notional": validation.notional,
        "min_notional": validation.min_notional,
        "order_amount": validation.order_amount,
        "max_single_order_usdt": validation.max_single_order_usdt,
        "usdt_available_balance": validation.usdt_available_balance,
        "balance_error": balance_error,
        "exchange_test_order": None,
        "rules": {
            "tick_size": rules.tick_size,
            "step_size": rules.step_size,
            "min_qty": rules.min_qty,
            "max_qty": rules.max_qty,
            "min_notional": rules.effective_min_notional,
            "notional_max": rules.notional_max,
        },
    }
    if not validation.ok:
        return payload

    if side == "buy":
        exchange_result = client.create_test_order(
            symbol=exchange_symbol,
            side="BUY",
            order_type="MARKET",
            quote_order_qty=amount,
        )
    else:
        exchange_result = client.create_test_order(
            symbol=exchange_symbol,
            side="SELL",
            order_type="MARKET",
            quantity=validation.normalized_quantity,
        )

    payload["exchange_test_order"] = exchange_result
    payload["exchange_test_order_ok"] = bool(exchange_result.get("ok"))
    return payload


def _blocked_real_market_buy_payload(
    *,
    symbol: str,
    amount: float,
    reasons: list[str],
    settings: dict,
    execution_config,
) -> dict:
    safety = settings.get("safety", {})
    return {
        "ok": False,
        "blocked": True,
        "blocked_reasons": reasons,
        "symbol": symbol,
        "side": "buy",
        "type": "MARKET",
        "amount": amount,
        "validator_ok": False,
        "exchange_test_order_ok": False,
        "real_order_sent": False,
        "broker_called": False,
        "trade_written": False,
        "mode": execution_config.mode,
        "safety": {
            "allow_live_trading": execution_config.allow_live_trading,
            "live_execute_enabled": execution_config.live_execute_enabled,
            "real_order_method_enabled": bool(safety.get("real_order_method_enabled", False)),
            "max_single_order_usdt": execution_config.max_single_order_usdt,
        },
        "env": {
            LIVE_CONFIRM_ENV_VAR: os.environ.get(LIVE_CONFIRM_ENV_VAR) == REQUIRED_ENV_VALUE,
            EXECUTE_REAL_ENV_VAR: os.environ.get(EXECUTE_REAL_ENV_VAR) == REQUIRED_ENV_VALUE,
            FINAL_REAL_ORDER_ENV_VAR: os.environ.get(FINAL_REAL_ORDER_ENV_VAR) == REQUIRED_ENV_VALUE,
        },
    }


def _real_market_buy(symbol: str, amount: float) -> dict:
    symbol = symbol.upper()
    if amount <= 0:
        raise ValueError("--amount must be greater than 0")

    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    safety = settings.get("safety", {})
    blocked_reasons: list[str] = []
    if execution_config.mode != "live":
        blocked_reasons.append("app.mode must be live")
    if not execution_config.allow_live_trading:
        blocked_reasons.append("safety.allow_live_trading must be true")
    if not execution_config.live_execute_enabled:
        blocked_reasons.append("safety.live_execute_enabled must be true")
    if not bool(safety.get("real_order_method_enabled", False)):
        blocked_reasons.append("safety.real_order_method_enabled must be true")
    if execution_config.max_single_order_usdt < amount:
        blocked_reasons.append("max_single_order_usdt below amount")
    if os.environ.get(LIVE_CONFIRM_ENV_VAR) != REQUIRED_ENV_VALUE:
        blocked_reasons.append(f"{LIVE_CONFIRM_ENV_VAR} must be YES")
    if os.environ.get(EXECUTE_REAL_ENV_VAR) != REQUIRED_ENV_VALUE:
        blocked_reasons.append(f"{EXECUTE_REAL_ENV_VAR} must be YES")
    if os.environ.get(FINAL_REAL_ORDER_ENV_VAR) != REQUIRED_ENV_VALUE:
        blocked_reasons.append(f"{FINAL_REAL_ORDER_ENV_VAR} must be YES")

    if blocked_reasons:
        return _blocked_real_market_buy_payload(
            symbol=symbol,
            amount=amount,
            reasons=blocked_reasons,
            settings=settings,
            execution_config=execution_config,
        )

    client = BinanceClient(
        base_url=execution_config.exchange.base_url,
        timeout=execution_config.exchange.request_timeout_seconds,
        error_log_file=execution_config.error_log_file,
        recv_window=execution_config.exchange.recv_window,
    )
    ticker = client.get_ticker_price(symbol)
    raw_price = float(ticker["price"])
    symbol_info = client.get_symbol_info(symbol)
    rules = parse_symbol_rules(symbol, symbol_info)
    normalized_price = _round_down_to_step(raw_price, rules.tick_size)
    raw_quantity = amount / raw_price
    normalized_quantity = _round_down_to_step(raw_quantity, rules.step_size)
    usdt_balance, balance_error = _usdt_free_balance(client)
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
        max_single_order_usdt=execution_config.max_single_order_usdt,
        bot_status="running",
        usdt_available_balance=usdt_balance,
        rules=rules,
    )
    payload = {
        "ok": False,
        "blocked": False,
        "symbol": symbol,
        "side": "buy",
        "type": "MARKET",
        "amount": amount,
        "validator_ok": validation.ok,
        "validator_reason": validation.reason,
        "exchange_test_order_ok": False,
        "real_order_sent": False,
        "broker_called": False,
        "trade_written": False,
        "ticker_price": raw_price,
        "raw_quantity": raw_quantity,
        "normalized_price": validation.normalized_price,
        "normalized_quantity": validation.normalized_quantity,
        "notional": validation.notional,
        "min_notional": validation.min_notional,
        "order_amount": validation.order_amount,
        "max_single_order_usdt": validation.max_single_order_usdt,
        "usdt_available_balance": validation.usdt_available_balance,
        "balance_error": balance_error,
        "exchange_test_order": None,
        "real_order": None,
    }
    if not validation.ok:
        return payload

    test_order = client.create_test_order(
        symbol=symbol,
        side="BUY",
        order_type="MARKET",
        quote_order_qty=amount,
    )
    payload["exchange_test_order"] = test_order
    payload["exchange_test_order_ok"] = bool(test_order.get("ok"))
    if not test_order.get("ok"):
        return payload

    real_order = client.create_order(
        symbol=symbol,
        side="BUY",
        order_type="MARKET",
        quote_order_qty=amount,
    )
    payload["real_order"] = real_order
    payload["real_order_sent"] = bool(real_order.get("real_order_sent"))
    payload["ok"] = bool(real_order.get("ok"))
    if not real_order.get("ok"):
        return payload

    raw_response = real_order.get("raw_response") or {}
    executed_qty = float(raw_response.get("executedQty") or normalized_quantity)
    executed_amount = float(raw_response.get("cummulativeQuoteQty") or amount)
    average_price = executed_amount / executed_qty if executed_qty > 0 else normalized_price
    trade_id = StorageRepository().record_trade(
        symbol=symbol,
        side="BUY",
        quantity=executed_qty,
        price=average_price,
        amount=executed_amount,
        mode="live",
    )
    logger = LogRouter(
        system_log=execution_config.system_log_file,
        trade_log=execution_config.trade_log_file,
        error_log=execution_config.error_log_file,
        mode="live",
    )
    logger.log_trade(
        symbol=symbol,
        action="real_market_buy",
        reason="manual_status_command",
        quantity=executed_qty,
        price=average_price,
        amount=executed_amount,
        trade_id=trade_id,
        order_id=raw_response.get("orderId"),
    )
    payload["trade_written"] = True
    payload["trade_id"] = trade_id
    payload["executed_quantity"] = executed_qty
    payload["executed_amount"] = executed_amount
    payload["average_price"] = average_price
    return payload


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
        "--exchange-test-order",
        metavar="SYMBOL",
        help="Validate locally, then submit Binance /api/v3/order/test without creating a real order.",
    )
    parser.add_argument(
        "--real-market-buy",
        metavar="SYMBOL",
        help="Manually submit a real MARKET BUY after all safety gates, validation, and exchange test order pass.",
    )
    parser.add_argument(
        "--side",
        choices=("buy", "sell"),
        help="Order side for --validate-order or --exchange-test-order.",
    )
    parser.add_argument(
        "--amount",
        type=float,
        help="Quote amount in USDT for buy, or base quantity for sell.",
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
    if args.exchange_test_order:
        if args.side is None or args.amount is None:
            parser.error("--exchange-test-order requires --side and --amount")
        payload = _exchange_test_order(args.exchange_test_order, args.side, args.amount)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.real_market_buy:
        if args.amount is None:
            parser.error("--real-market-buy requires --amount")
        if args.side is not None and args.side != "buy":
            parser.error("--real-market-buy only supports --side buy")
        payload = _real_market_buy(args.real_market_buy, args.amount)
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
