from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.secrets import load_futures_binance_readonly_credentials  # noqa: E402
from futures_bot.config_loader import load_futures_config  # noqa: E402
from futures_bot.exchange.binance_futures_client import BinanceFuturesClient  # noqa: E402
from futures_bot.execution.futures_paper_broker import FuturesPaperBroker  # noqa: E402
from futures_bot.risk.futures_risk import check_futures_pre_open_risk  # noqa: E402
from futures_bot.status_futures import build_strategy_signal_payload  # noqa: E402
from observability.event_logger import StructuredLogger  # noqa: E402


FUTURES_LOG_FILE = "logs/futures.log"


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_nonzero_position(position: dict[str, Any]) -> bool:
    return _to_float(position.get("positionAmt")) != 0.0


def _sum_unrealized_pnl(positions: list[dict[str, Any]]) -> float:
    return sum(
        _to_float(position.get("unRealizedProfit", position.get("unrealizedProfit")))
        for position in positions
    )


def _log_startup_sync(**payload: Any) -> None:
    StructuredLogger(FUTURES_LOG_FILE).log(
        action="futures_startup_readonly_sync",
        symbol="-",
        **payload,
    )


def _log_strategy_event(**payload: Any) -> None:
    payload.setdefault("event_type", "futures_strategy_signal")
    payload.setdefault("symbol", "-")
    StructuredLogger(FUTURES_LOG_FILE).log(**payload)


def run_startup_readonly_sync(config) -> dict[str, Any]:
    credentials = load_futures_binance_readonly_credentials()
    if not credentials.configured:
        summary = {
            "account_query_ok": False,
            "skipped": True,
            "reason": "futures_api_key_missing",
            "nonzero_positions_count": 0,
            "open_orders_count": 0,
            "total_unrealized_pnl": 0.0,
        }
        print("[futures_startup_sync] skipped: futures_api_key_missing")
        _log_startup_sync(**summary)
        return summary


def run_paper_strategy_cycle(config) -> list[dict[str, Any]]:
    broker = FuturesPaperBroker()
    results: list[dict[str, Any]] = []

    for symbol in config.enabled_symbols:
        symbol_config = config.symbols[symbol]
        signal_payload = build_strategy_signal_payload(symbol)
        action = str(signal_payload.get("action", "HOLD"))
        result: dict[str, Any] = {
            "symbol": symbol,
            "strategy": symbol_config.strategy,
            "action": action,
            "reason": signal_payload.get("reason"),
            "paper_action": "none",
            "signal": signal_payload,
        }

        if signal_payload.get("ok") is False:
            result["paper_action"] = "skipped_signal_error"
            print(f"[futures_strategy] {symbol} skipped: {signal_payload.get('reason')}")
            _log_strategy_event(**result)
            results.append(result)
            continue

        mark_price = _to_float(signal_payload.get("mark_price"))
        funding_rate = _to_float(signal_payload.get("funding_rate"))

        if action == "LONG":
            risk_result = check_futures_pre_open_risk(
                symbol=symbol,
                side="long",
                margin_amount=symbol_config.margin_amount,
                leverage=symbol_config.leverage,
                mark_price=mark_price,
                funding_rate=funding_rate,
                account_equity=100.0,
            )
            result["risk"] = {
                "ok": risk_result.ok,
                "reason": risk_result.reason,
                "position_ratio": risk_result.position_ratio,
            }
            if risk_result.ok:
                position = broker.open_position(
                    symbol=symbol,
                    side="long",
                    margin=symbol_config.margin_amount,
                    leverage=symbol_config.leverage,
                    price=mark_price,
                )
                result["paper_action"] = "opened"
                result["position"] = position.to_dict()
                print(f"[futures_strategy] {symbol} LONG opened in paper")
            else:
                result["paper_action"] = "risk_rejected"
                print(f"[futures_strategy] {symbol} LONG rejected: {risk_result.reason}")
        elif action == "CLOSE":
            try:
                position = broker.close_position(symbol, mark_price)
            except (KeyError, ValueError):
                result["paper_action"] = "close_skipped_no_position"
                print(f"[futures_strategy] {symbol} CLOSE skipped: no paper position")
            else:
                result["paper_action"] = "closed"
                result["position"] = position.to_dict()
                print(f"[futures_strategy] {symbol} CLOSE closed in paper")
        else:
            print(f"[futures_strategy] {symbol} HOLD: {signal_payload.get('reason')}")

        _log_strategy_event(**result)
        results.append(result)

    return results

    client = BinanceFuturesClient(
        base_url=config.futures.base_url,
        timeout=config.futures.request_timeout_seconds,
        credentials=credentials,
        log_file=FUTURES_LOG_FILE,
    )

    try:
        balance_payload = client.get_futures_balance()
        positions_payload = client.get_futures_positions()
        open_orders_payload = client.get_futures_open_orders()

        if not isinstance(balance_payload, list):
            raise RuntimeError("Futures balance response was not a list")
        if not isinstance(positions_payload, list):
            raise RuntimeError("Futures positions response was not a list")
        if not isinstance(open_orders_payload, list):
            raise RuntimeError("Futures open orders response was not a list")

        nonzero_positions = [
            position
            for position in positions_payload
            if isinstance(position, dict) and _is_nonzero_position(position)
        ]
        total_unrealized_pnl = _sum_unrealized_pnl(nonzero_positions)
        summary = {
            "account_query_ok": True,
            "skipped": False,
            "reason": None,
            "nonzero_positions_count": len(nonzero_positions),
            "open_orders_count": len(open_orders_payload),
            "total_unrealized_pnl": total_unrealized_pnl,
        }
        print("[futures_startup_sync] account_query_ok")
        print(f"[futures_startup_sync] nonzero_positions_count={len(nonzero_positions)}")
        print(f"[futures_startup_sync] open_orders_count={len(open_orders_payload)}")
        print(f"[futures_startup_sync] total_unrealized_pnl={total_unrealized_pnl}")
        if nonzero_positions:
            print("[futures_startup_sync] warning: existing_nonzero_positions_readonly_only")
        if open_orders_payload:
            print("[futures_startup_sync] warning: existing_open_orders_readonly_only")
        _log_startup_sync(**summary)
        return summary
    except Exception as exc:
        summary = {
            "account_query_ok": False,
            "skipped": False,
            "reason": str(exc),
            "nonzero_positions_count": 0,
            "open_orders_count": 0,
            "total_unrealized_pnl": 0.0,
        }
        print(f"[futures_startup_sync] account_query_failed: {exc}")
        _log_startup_sync(**summary)
        return summary


def main() -> int:
    config = load_futures_config()
    enabled_symbols = config.enabled_symbols

    print(f"futures settings path: {config.settings_path.resolve()}")
    print(f"futures symbols path: {config.symbols_path.resolve()}")
    print(f"app.mode: {config.app.mode}")
    print(f"enabled futures symbols: {', '.join(enabled_symbols) or '-'}")
    print(f"base_url: {config.futures.base_url}")
    print("stage: futures trend_long strategy / paper only")
    run_startup_readonly_sync(config)

    if not enabled_symbols:
        print("[futures_idle] no_enabled_symbols")
        return 0

    if config.app.mode != "paper":
        print("[futures_strategy] skipped: futures strategy loop only runs in paper mode")
        return 0

    run_paper_strategy_cycle(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
