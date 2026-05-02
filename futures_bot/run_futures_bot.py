from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.secrets import load_futures_binance_readonly_credentials  # noqa: E402
from futures_bot.config_loader import load_futures_config  # noqa: E402
from futures_bot.exchange.binance_futures_client import BinanceFuturesClient  # noqa: E402
from futures_bot.execution.futures_paper_broker import FuturesPaperBroker  # noqa: E402
from futures_bot.risk.futures_risk import check_futures_pre_open_risk  # noqa: E402
from futures_bot.strategy.base import CLOSE, CLOSE_FULL, CLOSE_PARTIAL_30, CLOSE_PARTIAL_50, HOLD, LONG  # noqa: E402
from futures_bot.strategy.registry import get_strategy  # noqa: E402
from observability.event_logger import StructuredLogger  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FUTURES_LOG_FILE = PROJECT_ROOT / "logs" / "futures.log"
FUTURES_LOOP_STATE_PATH = PROJECT_ROOT / "data" / "futures_loop_state.json"
FALLBACK_ACCOUNT_EQUITY = 100.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _log(event_action: str, *, symbol: str = "-", **payload: Any) -> None:
    event = dict(payload)
    event_symbol = event.pop("symbol", symbol)
    if "action" in event:
        event["signal_action"] = event.pop("action")
    StructuredLogger(str(FUTURES_LOG_FILE)).log(
        action=event_action,
        symbol=event_symbol,
        **event,
    )


def _load_loop_state() -> dict[str, Any]:
    if not FUTURES_LOOP_STATE_PATH.exists():
        return {"last_loop_at": None, "signals": {}, "last_processed_bars": {}}
    try:
        payload = json.loads(FUTURES_LOOP_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_loop_at": None, "signals": {}, "last_processed_bars": {}}
    if not isinstance(payload, dict):
        return {"last_loop_at": None, "signals": {}, "last_processed_bars": {}}
    payload.setdefault("signals", {})
    payload.setdefault("last_processed_bars", {})
    return payload


def _save_loop_state(state: dict[str, Any]) -> None:
    FUTURES_LOOP_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FUTURES_LOOP_STATE_PATH.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _position_for_symbol(broker: FuturesPaperBroker, symbol: str):
    symbol = symbol.upper()
    for position in broker.get_positions():
        if position.symbol.upper() == symbol:
            return position
    return None


def _latest_funding_rate(client: BinanceFuturesClient, symbol: str, mark_payload: Any) -> float | None:
    funding_rate = None
    if isinstance(mark_payload, dict):
        funding_rate = _float_or_none(mark_payload.get("lastFundingRate"))
    if funding_rate is not None:
        return funding_rate
    funding_payload = client.get_funding_rate(symbol, limit=1)
    if isinstance(funding_payload, list) and funding_payload:
        return _float_or_none(funding_payload[0].get("fundingRate"))
    return None


def _latest_bar_time(signal_klines: list[Any]) -> str | None:
    if not signal_klines:
        return None
    latest = signal_klines[-1]
    if isinstance(latest, (list, tuple)) and latest:
        return str(latest[0])
    return None


def _signal_record(
    *,
    symbol: str,
    strategy: str,
    action: str,
    reason: str,
    trend_timeframe: str,
    signal_timeframe: str,
    mark_price: float | None,
    funding_rate: float | None,
    signal_bar_time: str | None,
    paper_action: str,
    error: str | None = None,
) -> dict[str, Any]:
    record = {
        "symbol": symbol,
        "strategy": strategy,
        "action": action,
        "reason": reason,
        "trend_timeframe": trend_timeframe,
        "signal_timeframe": signal_timeframe,
        "mark_price": mark_price,
        "funding_rate": funding_rate,
        "signal_bar_time": signal_bar_time,
        "paper_action": paper_action,
        "updated_at": _utc_now(),
    }
    if error is not None:
        record["error"] = error
    return record


def _load_account_equity(client: BinanceFuturesClient) -> tuple[float, str]:
    credentials = load_futures_binance_readonly_credentials()
    if not credentials.configured:
        return FALLBACK_ACCOUNT_EQUITY, "fallback_no_futures_api_key"

    try:
        payload = client.get_futures_balance()
    except Exception:
        return FALLBACK_ACCOUNT_EQUITY, "fallback_account_query_error"

    if not isinstance(payload, list):
        return FALLBACK_ACCOUNT_EQUITY, "fallback_unexpected_balance_payload"
    for item in payload:
        if not isinstance(item, dict) or item.get("asset") != "USDT":
            continue
        for field_name in ("marginBalance", "walletBalance", "crossWalletBalance"):
            value = _float_or_none(item.get(field_name))
            if value is not None:
                return value, f"futures_balance_{field_name}"
    return FALLBACK_ACCOUNT_EQUITY, "fallback_missing_usdt_equity"


def _funding_rate_limit_for_strategy(config, strategy_name: str) -> tuple[float, str]:
    if config.app.mode == "paper" and strategy_name == "trend_long_test":
        return (
            config.risk.paper_test_max_funding_rate_abs,
            "paper_test_max_funding_rate_abs",
        )
    return config.risk.max_funding_rate_abs, "max_funding_rate_abs"


def _paper_only_strategy_violations(config) -> list[str]:
    violations: list[str] = []
    if config.app.mode == "paper":
        return violations
    for symbol in config.enabled_symbols:
        symbol_config = config.symbols[symbol]
        strategy = get_strategy(symbol_config.strategy)
        if bool(getattr(strategy, "paper_only", False)):
            violations.append(f"{symbol}:{strategy.name}")
    return violations


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
        _log("futures_startup_readonly_sync", **summary)
        return summary

    client = BinanceFuturesClient(
        base_url=config.futures.base_url,
        timeout=config.futures.request_timeout_seconds,
        credentials=credentials,
        log_file=str(FUTURES_LOG_FILE),
    )
    try:
        balance_payload = client.get_futures_balance()
        positions_payload = client.get_futures_positions()
        open_orders_payload = client.get_futures_open_orders()
        nonzero_positions = [
            position
            for position in positions_payload
            if isinstance(position, dict) and _to_float(position.get("positionAmt")) != 0.0
        ] if isinstance(positions_payload, list) else []
        summary = {
            "account_query_ok": isinstance(balance_payload, list),
            "skipped": False,
            "reason": None,
            "nonzero_positions_count": len(nonzero_positions),
            "open_orders_count": len(open_orders_payload) if isinstance(open_orders_payload, list) else 0,
            "total_unrealized_pnl": sum(
                _to_float(position.get("unRealizedProfit", position.get("unrealizedProfit")))
                for position in nonzero_positions
            ),
        }
    except Exception as exc:
        summary = {
            "account_query_ok": False,
            "skipped": False,
            "reason": str(exc),
            "nonzero_positions_count": 0,
            "open_orders_count": 0,
            "total_unrealized_pnl": 0.0,
        }
    _log("futures_startup_readonly_sync", **summary)
    return summary


def run_paper_strategy_cycle(config) -> list[dict[str, Any]]:
    broker = FuturesPaperBroker()
    client = BinanceFuturesClient(
        base_url=config.futures.base_url,
        timeout=config.futures.request_timeout_seconds,
        log_file=str(FUTURES_LOG_FILE),
    )
    loop_state = _load_loop_state()
    loop_started_at = _utc_now()
    loop_state["last_loop_at"] = loop_started_at
    loop_state.setdefault("signals", {})
    loop_state.setdefault("last_processed_bars", {})
    results: list[dict[str, Any]] = []

    _log("futures_loop_start", enabled_symbols=list(config.enabled_symbols), loop_started_at=loop_started_at)

    for symbol in config.enabled_symbols:
        symbol_config = config.symbols[symbol]
        try:
            trend_klines = client.get_klines(symbol, symbol_config.trend_timeframe, limit=300)
            signal_klines = client.get_klines(symbol, symbol_config.signal_timeframe, limit=300)
            mark_payload = client.get_mark_price(symbol)
            mark_price = _float_or_none(mark_payload.get("markPrice")) if isinstance(mark_payload, dict) else None
            funding_rate = _latest_funding_rate(client, symbol, mark_payload)
            signal_bar_time = _latest_bar_time(signal_klines)

            if mark_price is None:
                raise ValueError("missing_mark_price")
            if funding_rate is None:
                raise ValueError("missing_funding_rate")

            existing_position = _position_for_symbol(broker, symbol)
            if existing_position is not None:
                updated_position = broker.update_mark_price(symbol, mark_price)
                existing_position = updated_position

            strategy = get_strategy(symbol_config.strategy)
            paper_only = bool(getattr(strategy, "paper_only", False))
            max_funding_rate_abs, funding_rate_limit_source = _funding_rate_limit_for_strategy(
                config,
                symbol_config.strategy,
            )
            signal = strategy.generate_signal(
                symbol=symbol,
                trend_klines=trend_klines,
                signal_klines=signal_klines,
                mark_price=mark_price,
                funding_rate=funding_rate,
                trend_timeframe=symbol_config.trend_timeframe,
                signal_timeframe=symbol_config.signal_timeframe,
                max_funding_rate_abs=max_funding_rate_abs,
            )
            signal_action = signal.action
            record = _signal_record(
                symbol=symbol,
                strategy=strategy.name,
                action=signal_action,
                reason=signal.reason,
                trend_timeframe=symbol_config.trend_timeframe,
                signal_timeframe=symbol_config.signal_timeframe,
                mark_price=mark_price,
                funding_rate=funding_rate,
                signal_bar_time=signal_bar_time,
                paper_action="none",
            )
            record["funding_rate_limit_used"] = max_funding_rate_abs
            record["funding_rate_limit_source"] = funding_rate_limit_source
            record["app_mode"] = config.app.mode
            record["paper_only"] = paper_only
            loop_state["signals"][symbol] = record
            _log("futures_signal", **record)

            if signal_action == HOLD:
                record["paper_action"] = "hold"
                _log("futures_signal_hold", **record)
                if signal.reason == "funding_rate_exceeds_max_abs":
                    print(
                        f"[futures_strategy] {symbol} HOLD: {signal.reason} "
                        f"(funding_rate={funding_rate}, "
                        f"limit={max_funding_rate_abs}, "
                        f"source={funding_rate_limit_source})"
                    )
                else:
                    print(f"[futures_strategy] {symbol} HOLD: {signal.reason}")
                results.append(record)
                continue

            processed_key = f"{symbol}:{symbol_config.signal_timeframe}"
            last_processed_bar = loop_state["last_processed_bars"].get(processed_key)

            if signal_action == LONG:
                if signal_bar_time is not None and last_processed_bar == signal_bar_time:
                    record["paper_action"] = "duplicate_bar_skipped"
                    _log("futures_duplicate_bar_skipped", **record)
                    print(f"[futures_strategy] {symbol} LONG skipped: duplicate signal bar")
                    results.append(record)
                    continue

                if existing_position is not None:
                    record["paper_action"] = "skipped_existing_position"
                    _log("futures_duplicate_bar_skipped", **record)
                    print(f"[futures_strategy] {symbol} LONG skipped: paper position exists")
                    results.append(record)
                    continue

                account_equity, account_equity_source = _load_account_equity(client)
                risk_result = check_futures_pre_open_risk(
                    symbol=symbol,
                    side="long",
                    margin_amount=symbol_config.margin_amount,
                    leverage=symbol_config.leverage,
                    mark_price=mark_price,
                    funding_rate=funding_rate,
                    account_equity=account_equity,
                    max_funding_rate_abs_override=max_funding_rate_abs,
                )
                record["risk"] = {
                    "ok": risk_result.ok,
                    "reason": risk_result.reason,
                    "position_ratio": risk_result.position_ratio,
                    "account_equity": account_equity,
                    "account_equity_source": account_equity_source,
                }
                if not risk_result.ok:
                    record["paper_action"] = "risk_blocked"
                    _log("futures_risk_blocked", **record)
                    if risk_result.reason == "funding_rate_exceeds_max_abs":
                        print(
                            f"[futures_strategy] {symbol} LONG blocked: {risk_result.reason} "
                            f"(funding_rate={funding_rate}, "
                            f"limit={max_funding_rate_abs}, "
                            f"source={funding_rate_limit_source})"
                        )
                    else:
                        print(f"[futures_strategy] {symbol} LONG blocked: {risk_result.reason}")
                    results.append(record)
                    continue

                position = broker.open_position(
                    symbol=symbol,
                    side="long",
                    margin=symbol_config.margin_amount,
                    leverage=symbol_config.leverage,
                    price=mark_price,
                    entry_bar_index=len(signal_klines) - 1,
                )
                record["paper_action"] = "opened"
                record["position"] = position.to_dict()
                loop_state["last_processed_bars"][processed_key] = signal_bar_time
                _log("futures_paper_open", **record)
                print(f"[futures_strategy] {symbol} LONG opened in paper")
            elif signal_action in {CLOSE, CLOSE_FULL}:
                if existing_position is None:
                    record["paper_action"] = "close_skipped_no_position"
                    _log("futures_signal_hold", **record)
                    print(f"[futures_strategy] {symbol} CLOSE skipped: no paper position")
                    results.append(record)
                    continue

                closed_position = broker.close_position(symbol, mark_price)
                record["paper_action"] = "closed"
                record["position"] = closed_position.to_dict()
                record["realized_pnl"] = closed_position.unrealized_pnl
                loop_state["last_processed_bars"][processed_key] = signal_bar_time
                _log("futures_paper_close", **record)
                print(f"[futures_paper_close] {symbol} CLOSE closed in paper")
            elif signal_action == CLOSE_PARTIAL_30:
                if existing_position is None:
                    record["paper_action"] = "partial_close_skipped_no_position"
                    _log("futures_signal_hold", **record)
                    print(f"[futures_strategy] {symbol} partial close skipped: no paper position")
                    results.append(record)
                    continue
                if existing_position.partial1_done:
                    record["paper_action"] = "partial1_skipped_already_done"
                    _log("futures_signal_hold", **record)
                    print(f"[futures_strategy] {symbol} partial1 skipped: already done")
                    results.append(record)
                    continue
                partial_position = broker.close_partial(symbol, config.risk.partial1_sell_pct, mark_price)
                partial_position.partial1_done = True
                broker.save_state()
                record["paper_action"] = "partial_closed"
                record["sell_pct"] = config.risk.partial1_sell_pct
                record["position"] = partial_position.to_dict()
                loop_state["last_processed_bars"][processed_key] = signal_bar_time
                _log("futures_paper_partial_close", **record)
                print(f"[futures_paper_close] {symbol} CLOSE_PARTIAL_30 closed in paper")
            elif signal_action == CLOSE_PARTIAL_50:
                if existing_position is None:
                    record["paper_action"] = "partial_close_skipped_no_position"
                    _log("futures_signal_hold", **record)
                    print(f"[futures_strategy] {symbol} partial close skipped: no paper position")
                    results.append(record)
                    continue
                if existing_position.partial2_done:
                    record["paper_action"] = "partial2_skipped_already_done"
                    _log("futures_signal_hold", **record)
                    print(f"[futures_strategy] {symbol} partial2 skipped: already done")
                    results.append(record)
                    continue
                partial_position = broker.close_partial(symbol, config.risk.partial2_sell_pct, mark_price)
                partial_position.partial2_done = True
                broker.save_state()
                record["paper_action"] = "partial_closed"
                record["sell_pct"] = config.risk.partial2_sell_pct
                record["position"] = partial_position.to_dict()
                loop_state["last_processed_bars"][processed_key] = signal_bar_time
                _log("futures_paper_partial_close", **record)
                print(f"[futures_paper_close] {symbol} CLOSE_PARTIAL_50 closed in paper")
            else:
                record["paper_action"] = "unknown_signal_hold"
                _log("futures_signal_hold", **record)
                print(f"[futures_strategy] {symbol} HOLD: unknown signal {signal_action}")

            results.append(record)
        except Exception as exc:
            error_record = _signal_record(
                symbol=symbol,
                strategy=symbol_config.strategy,
                action="HOLD",
                reason="futures_market_data_error",
                trend_timeframe=symbol_config.trend_timeframe,
                signal_timeframe=symbol_config.signal_timeframe,
                mark_price=None,
                funding_rate=None,
                signal_bar_time=None,
                paper_action="market_data_error",
                error=str(exc),
            )
            loop_state["signals"][symbol] = error_record
            _log("futures_market_data_error", **error_record)
            print(f"[futures_strategy] {symbol} error: {exc}")
            results.append(error_record)
            continue

    _save_loop_state(loop_state)
    return results


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Futures paper strategy loop.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one Futures paper loop cycle and exit.",
    )
    return parser.parse_args(argv)


def _print_startup(config) -> None:
    print(f"futures settings path: {config.settings_path.resolve()}")
    print(f"futures symbols path: {config.symbols_path.resolve()}")
    print(f"app.mode: {config.app.mode}")
    print(f"enabled futures symbols: {', '.join(config.enabled_symbols) or '-'}")
    print(f"base_url: {config.futures.base_url}")
    print("stage: futures automatic paper loop / paper only")


def run_loop(*, once: bool = False) -> int:
    startup_synced = False
    while True:
        config = load_futures_config()
        _print_startup(config)
        paper_only_violations = _paper_only_strategy_violations(config)
        if paper_only_violations:
            reason = "paper_only_strategy_not_allowed"
            print(
                "[futures_startup_refused] "
                f"{reason}: {', '.join(paper_only_violations)}"
            )
            _log(
                "futures_startup_refused",
                enabled_symbols=list(config.enabled_symbols),
                app_mode=config.app.mode,
                reason=reason,
                violations=paper_only_violations,
            )
            return 1
        if not startup_synced:
            run_startup_readonly_sync(config)
            startup_synced = True

        if not config.enabled_symbols:
            print("[futures_idle] no_enabled_symbols")
            loop_started_at = _utc_now()
            loop_state = _load_loop_state()
            loop_state["last_loop_at"] = loop_started_at
            loop_state["signals"] = {}
            _save_loop_state(loop_state)
            _log("futures_loop_start", enabled_symbols=[], idle=True, loop_started_at=loop_started_at)
            if once:
                return 0
        elif config.app.mode != "paper":
            print("[futures_strategy] skipped: futures automatic loop only runs in paper mode")
            loop_started_at = _utc_now()
            loop_state = _load_loop_state()
            loop_state["last_loop_at"] = loop_started_at
            _save_loop_state(loop_state)
            _log("futures_loop_start", enabled_symbols=list(config.enabled_symbols), skipped=True, reason="not_paper_mode")
            if once:
                return 0
        else:
            run_paper_strategy_cycle(config)
            if once:
                return 0

        time.sleep(config.app.polling_interval_seconds)


def main() -> int:
    args = parse_args(sys.argv[1:])
    try:
        return run_loop(once=args.once)
    except KeyboardInterrupt:
        print("[futures_shutdown] keyboard_interrupt")
        _log("futures_shutdown", reason="keyboard_interrupt")
        return 0


if __name__ == "__main__":
    sys.exit(main())
