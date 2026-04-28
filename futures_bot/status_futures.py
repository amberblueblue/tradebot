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


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FUTURES_PAPER_STATE_PATH = PROJECT_ROOT / "runtime" / "futures_paper_positions.json"


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
        "max_position_ratio": risk.max_position_ratio,
        "min_liquidation_distance_pct": risk.min_liquidation_distance_pct,
        "max_funding_rate_abs": risk.max_funding_rate_abs,
        "max_consecutive_losing_trades": risk.max_consecutive_losing_trades,
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
        "max_position_ratio": risk.max_position_ratio,
        "min_liquidation_distance_pct": risk.min_liquidation_distance_pct,
        "max_funding_rate_abs": risk.max_funding_rate_abs,
    }


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


def _load_futures_paper_state() -> list[dict[str, Any]]:
    if not FUTURES_PAPER_STATE_PATH.exists():
        return []
    payload = json.loads(FUTURES_PAPER_STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    positions = payload.get("positions", [])
    if not isinstance(positions, list):
        return []
    return [position for position in positions if isinstance(position, dict)]


def _save_futures_paper_state(broker: FuturesPaperBroker) -> None:
    FUTURES_PAPER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "positions": [
            position.to_dict()
            for position in broker.get_positions()
        ],
    }
    FUTURES_PAPER_STATE_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _load_futures_paper_broker() -> FuturesPaperBroker:
    broker = FuturesPaperBroker()
    for position in _load_futures_paper_state():
        entry_price = _float_or_none(position.get("entry_price"))
        margin = _float_or_none(position.get("margin"))
        leverage = _float_or_none(position.get("leverage"))
        if entry_price is None or margin is None or leverage is None:
            continue
        broker.open_position(
            symbol=str(position.get("symbol", "")),
            side=str(position.get("side", "")),
            margin=margin,
            leverage=leverage,
            price=entry_price,
        )
        mark_price = _float_or_none(position.get("mark_price"))
        if mark_price is not None:
            broker.update_mark_price(str(position.get("symbol", "")), mark_price)
    return broker


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

    broker = _load_futures_paper_broker()
    position = broker.open_position(
        symbol=symbol,
        side=side,
        margin=margin_amount,
        leverage=leverage,
        price=mark_price,
    )
    _save_futures_paper_state(broker)
    return {
        "ok": True,
        "reason": "paper_position_opened",
        "position": position.to_dict(),
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

    broker = _load_futures_paper_broker()
    try:
        broker.update_mark_price(symbol, mark_price)
    except ValueError as exc:
        return {
            "symbol": symbol,
            "mark_price": mark_price,
            "positions": [],
            "ok": False,
            "reason": str(exc),
        }

    _save_futures_paper_state(broker)
    return {
        "symbol": symbol,
        "mark_price": mark_price,
        "positions": [
            position.to_dict()
            for position in broker.get_positions()
        ],
        "ok": True,
        "reason": "paper_mark_price_updated",
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
        "--market-data",
        metavar="SYMBOL",
        help="Fetch public Binance USD-M Futures market data for a symbol.",
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
        elif args.market_data:
            payload = build_market_data_payload(args.market_data)
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
