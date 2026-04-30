from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.secrets import load_futures_binance_readonly_credentials  # noqa: E402
from futures_bot.config_loader import load_futures_config  # noqa: E402
from futures_bot.exchange.binance_futures_client import BinanceFuturesClient  # noqa: E402
from futures_bot.exchange.futures_rules import parse_futures_symbol_rules  # noqa: E402
from futures_bot.execution.futures_paper_broker import FuturesPaperBroker  # noqa: E402
from futures_bot.risk.futures_risk import check_futures_pre_open_risk  # noqa: E402
from futures_bot.strategy.registry import get_strategy  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FUTURES_STRATEGY_SIGNALS_PATH = PROJECT_ROOT / "data" / "futures_strategy_signals.json"

paper_broker = FuturesPaperBroker()


def build_status_payload() -> dict[str, object]:
    config = load_futures_config()
    enabled_symbols = list(config.enabled_symbols)

    return {
        "mode": config.app.mode,
        "base_url": config.futures.base_url,
        "enabled_symbols": enabled_symbols,
        "symbols_count": len(enabled_symbols),
        "live_allowed": config.safety.allow_live_trading,
        "public_data_only": True,
    }


def build_risk_config_payload() -> dict[str, object]:
    risk = load_futures_config().risk
    return {
        "max_leverage": risk.max_leverage,
        "max_margin_per_trade_usdt": risk.max_margin_per_trade_usdt,
        "max_single_order_usdt": risk.max_single_order_usdt,
        "max_position_ratio": risk.max_position_ratio,
        "min_liquidation_distance_pct": risk.min_liquidation_distance_pct,
        "max_funding_rate_abs": risk.max_funding_rate_abs,
        "paper_test_max_funding_rate_abs": risk.paper_test_max_funding_rate_abs,
        "max_consecutive_losing_trades": risk.max_consecutive_losing_trades,
        "stop_loss_pct": risk.stop_loss_pct,
        "partial1_sell_pct": risk.partial1_sell_pct,
        "partial2_sell_pct": risk.partial2_sell_pct,
        "big_candle_multiplier": risk.big_candle_multiplier,
        "big_candle_body_lookback": risk.big_candle_body_lookback,
        "profit_giveback_ratio": risk.profit_giveback_ratio,
        "profit_protection_trigger_pct": risk.profit_protection_trigger_pct,
    }


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_market_data_payload(symbol: str) -> dict[str, object]:
    symbol = symbol.upper()
    client = BinanceFuturesClient()

    try:
        symbol_info = client.get_symbol_info(symbol)
        rules = parse_futures_symbol_rules(symbol_info)
        ticker_payload = client.get_ticker_price(symbol)
        mark_payload = client.get_mark_price(symbol)
    except Exception as exc:
        return {
            "symbol": symbol,
            "error": "futures_public_market_data_error",
            "message": str(exc),
            "public_api_ok": False,
        }

    funding_rate = None
    funding_rate_error = None
    try:
        funding_payload = client.get_funding_rate(symbol, limit=1)
        if isinstance(funding_payload, list) and funding_payload:
            funding_rate = _float_or_none(funding_payload[0].get("fundingRate"))
    except Exception as exc:
        funding_rate_error = str(exc)

    payload: dict[str, object] = {
        "symbol": symbol,
        "ticker_price": _float_or_none(ticker_payload.get("price")),
        "mark_price": _float_or_none(mark_payload.get("markPrice")),
        "funding_rate": funding_rate,
        "next_funding_time": mark_payload.get("nextFundingTime"),
        "rules": {
            "tick_size": rules.tick_size,
            "step_size": rules.step_size,
            "min_qty": rules.min_qty,
            "min_notional": rules.min_notional,
        },
        "public_api_ok": True,
    }
    if funding_rate_error is not None:
        payload["funding_rate_error"] = funding_rate_error
    return payload


def _risk_thresholds_payload() -> dict[str, object]:
    risk = load_futures_config().risk
    return {
        "max_leverage": risk.max_leverage,
        "max_margin_per_trade_usdt": risk.max_margin_per_trade_usdt,
        "max_single_order_usdt": risk.max_single_order_usdt,
        "max_position_ratio": risk.max_position_ratio,
        "min_liquidation_distance_pct": risk.min_liquidation_distance_pct,
        "max_funding_rate_abs": risk.max_funding_rate_abs,
        "paper_test_max_funding_rate_abs": risk.paper_test_max_funding_rate_abs,
        "max_consecutive_losing_trades": risk.max_consecutive_losing_trades,
        "stop_loss_pct": risk.stop_loss_pct,
        "partial1_sell_pct": risk.partial1_sell_pct,
        "partial2_sell_pct": risk.partial2_sell_pct,
        "big_candle_multiplier": risk.big_candle_multiplier,
        "big_candle_body_lookback": risk.big_candle_body_lookback,
        "profit_giveback_ratio": risk.profit_giveback_ratio,
        "profit_protection_trigger_pct": risk.profit_protection_trigger_pct,
    }


def _funding_rate_limit_for_strategy(config, strategy_name: str) -> tuple[float, str]:
    if config.app.mode == "paper" and strategy_name == "trend_long_test":
        return (
            config.risk.paper_test_max_funding_rate_abs,
            "paper_test_max_funding_rate_abs",
        )
    return config.risk.max_funding_rate_abs, "max_funding_rate_abs"


def _is_missing_key_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("error") == "futures_api_key_missing"


def build_account_status_payload() -> dict[str, object]:
    credentials = load_futures_binance_readonly_credentials()
    credential_status = credentials.public_status()
    if not credentials.configured:
        return {
            "api_key_configured": credentials.api_key_configured,
            "api_secret_configured": credentials.api_secret_configured,
            "account_query_ok": False,
            "error": "Futures API key missing",
        }

    try:
        payload = BinanceFuturesClient(credentials=credentials).get_futures_balance()
    except Exception as exc:
        return {
            "api_key_configured": credentials.api_key_configured,
            "api_secret_configured": credentials.api_secret_configured,
            "account_query_ok": False,
            "error": str(exc),
        }

    if _is_missing_key_payload(payload):
        return {
            "api_key_configured": bool(credential_status["api_key_configured"]),
            "api_secret_configured": bool(credential_status["api_secret_configured"]),
            "account_query_ok": False,
            "error": payload.get("message", "Futures API key missing"),
        }

    return {
        "api_key_configured": credentials.api_key_configured,
        "api_secret_configured": credentials.api_secret_configured,
        "account_query_ok": True,
        "error": None,
    }


def _balance_row(balance: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset": balance.get("asset"),
        "walletBalance": balance.get("walletBalance"),
        "availableBalance": balance.get("availableBalance"),
        "marginBalance": balance.get("marginBalance"),
        "unrealizedProfit": balance.get("unrealizedProfit"),
    }


def build_balance_payload() -> dict[str, object]:
    credentials = load_futures_binance_readonly_credentials()
    if not credentials.configured:
        return {
            "balance": [],
            "query_ok": False,
            "error": "futures_api_key_missing",
            "message": "Futures API key missing",
        }

    payload = BinanceFuturesClient(credentials=credentials).get_futures_balance()
    if _is_missing_key_payload(payload):
        return {
            "balance": [],
            "query_ok": False,
            "error": payload.get("error"),
            "message": payload.get("message"),
        }
    if not isinstance(payload, list):
        return {
            "balance": [],
            "query_ok": False,
            "error": "unexpected_futures_balance_payload",
            "message": "Futures balance response was not a list",
        }
    return {
        "balance": [_balance_row(item) for item in payload if isinstance(item, dict)],
        "query_ok": True,
        "error": None,
    }


def _extract_usdt_account_equity(balance_payload: list[Any]) -> float | None:
    for item in balance_payload:
        if not isinstance(item, dict) or item.get("asset") != "USDT":
            continue
        for field_name in ("marginBalance", "walletBalance", "crossWalletBalance"):
            value = _float_or_none(item.get(field_name))
            if value is not None:
                return value
    return None


def _load_account_equity_for_dry_run(client: BinanceFuturesClient) -> tuple[float, str, str | None]:
    credentials = load_futures_binance_readonly_credentials()
    if not credentials.configured:
        return 100.0, "fallback_no_futures_api_key", "Futures API key missing"

    try:
        payload = client.get_futures_balance()
    except Exception as exc:
        return 100.0, "fallback_account_query_error", str(exc)

    if _is_missing_key_payload(payload):
        return 100.0, "fallback_no_futures_api_key", payload.get("message", "Futures API key missing")
    if not isinstance(payload, list):
        return 100.0, "fallback_unexpected_balance_payload", "Futures balance response was not a list"

    account_equity = _extract_usdt_account_equity(payload)
    if account_equity is None:
        return 100.0, "fallback_missing_usdt_equity", "USDT account equity not found"
    return account_equity, "futures_balance_margin_balance", None


def _position_payload(position: Any) -> dict[str, object]:
    return position.to_dict()


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


def _save_strategy_signal(payload: dict[str, object]) -> None:
    FUTURES_STRATEGY_SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, object] = {}
    if FUTURES_STRATEGY_SIGNALS_PATH.exists():
        try:
            loaded = json.loads(FUTURES_STRATEGY_SIGNALS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing[str(payload["symbol"])] = payload
    FUTURES_STRATEGY_SIGNALS_PATH.write_text(
        json.dumps(existing, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def build_strategy_signal_payload(symbol: str) -> dict[str, object]:
    symbol = symbol.upper()
    config = load_futures_config()
    symbol_config = config.symbols.get(symbol)
    if symbol_config is None:
        return {
            "symbol": symbol,
            "ok": False,
            "reason": "futures_symbol_not_configured",
        }

    strategy = get_strategy(symbol_config.strategy)
    paper_only = bool(getattr(strategy, "paper_only", False))
    max_funding_rate_abs, funding_rate_limit_source = _funding_rate_limit_for_strategy(
        config,
        symbol_config.strategy,
    )
    if paper_only and config.app.mode != "paper":
        return {
            "symbol": symbol,
            "strategy": strategy.name,
            "app_mode": config.app.mode,
            "paper_only": paper_only,
            "ok": False,
            "reason": "paper_only_strategy_not_allowed",
            "funding_rate_limit_used": max_funding_rate_abs,
            "funding_rate_limit_source": funding_rate_limit_source,
            "metadata": {
                "funding_rate": None,
                "funding_rate_limit_used": max_funding_rate_abs,
                "funding_rate_limit_source": funding_rate_limit_source,
                "paper_only": paper_only,
            },
        }

    client = BinanceFuturesClient(
        base_url=config.futures.base_url,
        timeout=config.futures.request_timeout_seconds,
    )
    try:
        trend_klines = client.get_klines(symbol, symbol_config.trend_timeframe, limit=300)
        signal_klines = client.get_klines(symbol, symbol_config.signal_timeframe, limit=300)
        mark_payload = client.get_mark_price(symbol)
        mark_price = _float_or_none(mark_payload.get("markPrice"))
        funding_rate = _latest_funding_rate(client, symbol, mark_payload)
    except Exception as exc:
        return {
            "symbol": symbol,
            "strategy": symbol_config.strategy,
            "ok": False,
            "reason": "futures_strategy_market_data_error",
            "app_mode": config.app.mode,
            "paper_only": paper_only,
            "funding_rate_limit_used": max_funding_rate_abs,
            "funding_rate_limit_source": funding_rate_limit_source,
            "error": str(exc),
        }

    if mark_price is None:
        return {
            "symbol": symbol,
            "strategy": symbol_config.strategy,
            "app_mode": config.app.mode,
            "paper_only": paper_only,
            "ok": False,
            "reason": "missing_mark_price",
            "funding_rate_limit_used": max_funding_rate_abs,
            "funding_rate_limit_source": funding_rate_limit_source,
        }
    if funding_rate is None:
        return {
            "symbol": symbol,
            "strategy": symbol_config.strategy,
            "app_mode": config.app.mode,
            "paper_only": paper_only,
            "ok": False,
            "reason": "missing_funding_rate",
            "mark_price": mark_price,
            "funding_rate_limit_used": max_funding_rate_abs,
            "funding_rate_limit_source": funding_rate_limit_source,
        }

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
    metadata = dict(signal.metadata or {})
    metadata.update(
        {
            "funding_rate": funding_rate,
            "funding_rate_limit_used": max_funding_rate_abs,
            "funding_rate_limit_source": funding_rate_limit_source,
            "paper_only": paper_only,
        }
    )
    payload: dict[str, object] = {
        "symbol": symbol,
        "strategy": strategy.name,
        "app_mode": config.app.mode,
        "paper_only": paper_only,
        "action": signal.action,
        "reason": signal.reason,
        "trend_timeframe": signal.trend_timeframe,
        "signal_timeframe": signal.signal_timeframe,
        "mark_price": mark_price,
        "funding_rate": funding_rate,
        "max_funding_rate_abs": max_funding_rate_abs,
        "funding_rate_limit_used": max_funding_rate_abs,
        "funding_rate_limit_source": funding_rate_limit_source,
        "confidence": signal.confidence,
        "metadata": metadata,
    }
    _save_strategy_signal(payload)
    return payload


def build_risk_check_payload(
    symbol: str,
    side: str,
    margin_amount: float,
    leverage: float,
) -> dict[str, object]:
    symbol = symbol.upper()
    normalized_side = side.lower()
    client = BinanceFuturesClient()

    try:
        mark_payload = client.get_mark_price(symbol)
        mark_price = _float_or_none(mark_payload.get("markPrice"))
        funding_rate = _float_or_none(mark_payload.get("lastFundingRate"))
        if funding_rate is None:
            funding_payload = client.get_funding_rate(symbol, limit=1)
            if isinstance(funding_payload, list) and funding_payload:
                funding_rate = _float_or_none(funding_payload[0].get("fundingRate"))
    except Exception as exc:
        return {
            "ok": False,
            "reason": "futures_public_market_data_error",
            "symbol": symbol,
            "side": normalized_side,
            "error": str(exc),
        }

    if mark_price is None:
        return {
            "ok": False,
            "reason": "missing_mark_price",
            "symbol": symbol,
            "side": normalized_side,
            "mark_price": None,
        }
    if funding_rate is None:
        return {
            "ok": False,
            "reason": "missing_funding_rate",
            "symbol": symbol,
            "side": normalized_side,
            "mark_price": mark_price,
            "funding_rate": None,
        }

    account_equity, account_equity_source, account_equity_warning = _load_account_equity_for_dry_run(client)
    risk_result = check_futures_pre_open_risk(
        symbol=symbol,
        side=normalized_side,
        margin_amount=margin_amount,
        leverage=leverage,
        mark_price=mark_price,
        funding_rate=funding_rate,
        account_equity=account_equity,
    )

    payload: dict[str, object] = {
        "ok": risk_result.ok,
        "reason": risk_result.reason,
        "symbol": risk_result.symbol,
        "side": normalized_side,
        "margin_amount": risk_result.margin_amount,
        "leverage": risk_result.leverage,
        "mark_price": mark_price,
        "funding_rate": risk_result.funding_rate,
        "account_equity": account_equity,
        "account_equity_source": account_equity_source,
        "position_ratio": risk_result.position_ratio,
        "liquidation_distance_pct": risk_result.liquidation_distance_pct,
        "config": _risk_thresholds_payload(),
        "details": risk_result.details,
    }
    if account_equity_warning is not None:
        payload["account_equity_warning"] = account_equity_warning
    return payload


def build_paper_open_payload(
    symbol: str,
    side: str,
    margin_amount: float,
    leverage: float,
) -> dict[str, object]:
    risk_payload = build_risk_check_payload(
        symbol=symbol,
        side=side,
        margin_amount=margin_amount,
        leverage=leverage,
    )
    if not risk_payload.get("ok"):
        return {
            "ok": False,
            "reason": risk_payload.get("reason", "futures_risk_rejected"),
            "position": None,
            "risk": risk_payload,
        }

    mark_price = _float_or_none(risk_payload.get("mark_price"))
    if mark_price is None:
        return {
            "ok": False,
            "reason": "missing_mark_price",
            "position": None,
            "risk": risk_payload,
        }

    position = paper_broker.open_position(
        symbol=symbol,
        side=side,
        margin=margin_amount,
        leverage=leverage,
        price=mark_price,
    )
    return {
        "ok": True,
        "reason": "paper_position_opened",
        "position": _position_payload(position),
        "risk": risk_payload,
    }


def build_paper_tick_payload(symbol: str) -> dict[str, object]:
    symbol = symbol.upper()
    client = BinanceFuturesClient()
    try:
        mark_payload = client.get_mark_price(symbol)
        mark_price = _float_or_none(mark_payload.get("markPrice"))
    except Exception as exc:
        return {
            "symbol": symbol,
            "mark_price": None,
            "positions": [],
            "ok": False,
            "reason": "futures_public_mark_price_error",
            "error": str(exc),
        }

    if mark_price is None:
        return {
            "symbol": symbol,
            "mark_price": None,
            "positions": [],
            "ok": False,
            "reason": "missing_mark_price",
        }

    try:
        paper_broker.update_mark_price(symbol, mark_price)
    except (KeyError, ValueError) as exc:
        return {
            "symbol": symbol,
            "mark_price": mark_price,
            "positions": [],
            "ok": False,
            "reason": str(exc),
        }

    return {
        "symbol": symbol,
        "mark_price": mark_price,
        "positions": [
            _position_payload(position)
            for position in paper_broker.get_positions()
        ],
        "ok": True,
        "reason": "paper_mark_price_updated",
    }


def build_paper_close_payload(symbol: str) -> dict[str, object]:
    symbol = symbol.upper()
    client = BinanceFuturesClient()
    try:
        mark_payload = client.get_mark_price(symbol)
        mark_price = _float_or_none(mark_payload.get("markPrice"))
    except Exception as exc:
        return {
            "closed": False,
            "realized_pnl": None,
            "symbol": symbol,
            "mark_price": None,
            "reason": "futures_public_mark_price_error",
            "error": str(exc),
        }

    if mark_price is None:
        return {
            "closed": False,
            "realized_pnl": None,
            "symbol": symbol,
            "mark_price": None,
            "reason": "missing_mark_price",
        }

    try:
        closed_position = paper_broker.close_position(symbol, mark_price)
    except (KeyError, ValueError) as exc:
        return {
            "closed": False,
            "realized_pnl": None,
            "symbol": symbol,
            "mark_price": mark_price,
            "reason": str(exc),
        }

    return {
        "ok": True,
        "reason": "paper_position_closed",
        "realized_pnl": closed_position.unrealized_pnl,
        "symbol": symbol,
        "mark_price": mark_price,
        "position": _position_payload(closed_position),
    }


def _position_is_nonzero(position: dict[str, Any]) -> bool:
    return (_float_or_none(position.get("positionAmt")) or 0.0) != 0.0


def _position_row(position: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": position.get("symbol"),
        "positionAmt": position.get("positionAmt"),
        "entryPrice": position.get("entryPrice"),
        "markPrice": position.get("markPrice"),
        "unrealizedProfit": position.get("unRealizedProfit", position.get("unrealizedProfit")),
        "liquidationPrice": position.get("liquidationPrice"),
        "leverage": position.get("leverage"),
        "marginType": position.get("marginType"),
        "positionSide": position.get("positionSide"),
    }


def build_positions_payload() -> dict[str, object]:
    credentials = load_futures_binance_readonly_credentials()
    if not credentials.configured:
        return {
            "positions": [],
            "query_ok": False,
            "error": "futures_api_key_missing",
            "message": "Futures API key missing",
        }

    payload = BinanceFuturesClient(credentials=credentials).get_futures_positions()
    if _is_missing_key_payload(payload):
        return {
            "positions": [],
            "query_ok": False,
            "error": payload.get("error"),
            "message": payload.get("message"),
        }
    if not isinstance(payload, list):
        return {
            "positions": [],
            "query_ok": False,
            "error": "unexpected_futures_positions_payload",
            "message": "Futures positions response was not a list",
        }
    return {
        "positions": [
            _position_row(item)
            for item in payload
            if isinstance(item, dict) and _position_is_nonzero(item)
        ],
        "query_ok": True,
        "error": None,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show Futures Bot status.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--account-status",
        action="store_true",
        help="Show Futures read-only API key status and account query health.",
    )
    mode_group.add_argument(
        "--balance",
        action="store_true",
        help="Show Futures read-only balances.",
    )
    mode_group.add_argument(
        "--positions",
        action="store_true",
        help="Show non-zero Futures read-only positions.",
    )
    mode_group.add_argument(
        "--risk-config",
        action="store_true",
        help="Show Futures risk configuration.",
    )
    mode_group.add_argument(
        "--risk-check",
        metavar="SYMBOL",
        help="Dry-run Futures pre-open risk checks for a symbol.",
    )
    mode_group.add_argument(
        "--paper-open",
        metavar="SYMBOL",
        help="Run risk checks, then simulate opening a Futures paper position.",
    )
    mode_group.add_argument(
        "--paper-tick",
        metavar="SYMBOL",
        help="Fetch mark price and update Futures paper position PnL.",
    )
    mode_group.add_argument(
        "--paper-close",
        metavar="SYMBOL",
        help="Fetch mark price and close a Futures paper position.",
    )
    mode_group.add_argument(
        "--market-data",
        metavar="SYMBOL",
        help="Fetch public Binance USD-M Futures market data for a symbol.",
    )
    mode_group.add_argument(
        "--strategy-signal",
        metavar="SYMBOL",
        help="Dry-run a Futures strategy signal for a symbol.",
    )
    parser.add_argument(
        "--side",
        choices=("long", "short"),
        default="long",
        help="Risk-check side.",
    )
    parser.add_argument(
        "--margin",
        type=float,
        help="Risk-check margin amount in USDT.",
    )
    parser.add_argument(
        "--leverage",
        type=float,
        help="Risk-check leverage.",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    try:
        if args.account_status:
            payload = build_account_status_payload()
        elif args.balance:
            payload = build_balance_payload()
        elif args.positions:
            payload = build_positions_payload()
        elif args.risk_config:
            payload = build_risk_config_payload()
        elif args.risk_check:
            if args.margin is None or args.leverage is None:
                raise ValueError("--risk-check requires --margin and --leverage")
            payload = build_risk_check_payload(
                args.risk_check,
                side=args.side,
                margin_amount=args.margin,
                leverage=args.leverage,
            )
        elif args.paper_open:
            if args.margin is None or args.leverage is None:
                raise ValueError("--paper-open requires --margin and --leverage")
            payload = build_paper_open_payload(
                args.paper_open,
                side=args.side,
                margin_amount=args.margin,
                leverage=args.leverage,
            )
        elif args.paper_tick:
            payload = build_paper_tick_payload(args.paper_tick)
        elif args.paper_close:
            payload = build_paper_close_payload(args.paper_close)
        elif args.market_data:
            payload = build_market_data_payload(args.market_data)
        elif args.strategy_signal:
            payload = build_strategy_signal_payload(args.strategy_signal)
        else:
            payload = build_status_payload()
    except Exception as exc:
        payload = {
            "error": "futures_config_error",
            "message": str(exc),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("public_api_ok") is False:
        return 1
    if "error" in payload and payload.get("error") not in {
        None,
        "futures_api_key_missing",
        "Futures API key missing",
    }:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
