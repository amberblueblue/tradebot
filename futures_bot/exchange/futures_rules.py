from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from futures_bot.config_loader import load_futures_config
from futures_bot.exchange.binance_futures_client import BinanceFuturesClient
from observability.event_logger import StructuredLogger


DEFAULT_STATUS = "TRADING"
DEFAULT_CONTRACT_TYPE = "PERPETUAL"
DEFAULT_PRICE_PRECISION = 2
DEFAULT_QUANTITY_PRECISION = 6
DEFAULT_TICK_SIZE = "0.01"
DEFAULT_STEP_SIZE = "0.000001"
DEFAULT_MIN_QTY = "0.000001"
DEFAULT_MAX_QTY = "100000000"
DEFAULT_MIN_NOTIONAL = "5"
DEFAULT_MARKET_MIN_QTY = "0"
DEFAULT_MARKET_MAX_QTY = "0"
DEFAULT_MARKET_STEP_SIZE = "0"
DEFAULT_RULES_CACHE_TTL_SECONDS = 3600
DEFAULT_FUTURES_LOG_FILE = "logs/futures.log"


@dataclass(frozen=True)
class FuturesSymbolRules:
    symbol: str
    status: str
    contract_type: str
    price_precision: int
    quantity_precision: int
    tick_size: float
    step_size: float
    min_qty: float
    max_qty: float
    min_notional: float
    market_min_qty: float
    market_max_qty: float
    market_step_size: float


@dataclass(frozen=True)
class CachedFuturesSymbolRules:
    rules: FuturesSymbolRules
    fetched_at: datetime


_RULES_CACHE: dict[str, CachedFuturesSymbolRules] = {}


def _logger() -> StructuredLogger:
    return StructuredLogger(DEFAULT_FUTURES_LOG_FILE)


def _log_rule_warning(symbol: str, reason: str, **extra: Any) -> None:
    _logger().log(
        symbol=symbol.upper(),
        action="futures_rule_warning",
        level="warning",
        reason=reason,
        **extra,
    )


def _decimal(value: Any, fallback: str, *, symbol: str, field: str) -> Decimal:
    if value in (None, ""):
        _log_rule_warning(symbol, f"missing_{field}", fallback=fallback)
        return Decimal(fallback)
    try:
        return Decimal(str(value))
    except Exception:
        _log_rule_warning(symbol, f"invalid_{field}", value=value, fallback=fallback)
        return Decimal(fallback)


def _integer(value: Any, fallback: int, *, symbol: str, field: str) -> int:
    if value in (None, ""):
        _log_rule_warning(symbol, f"missing_{field}", fallback=fallback)
        return fallback
    try:
        return int(value)
    except Exception:
        _log_rule_warning(symbol, f"invalid_{field}", value=value, fallback=fallback)
        return fallback


def _filters_by_type(symbol_info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    filters = symbol_info.get("filters", [])
    if not isinstance(filters, list):
        return {}
    return {
        str(item.get("filterType")): item
        for item in filters
        if isinstance(item, dict) and item.get("filterType")
    }


def _parse_min_notional(filters: dict[str, dict[str, Any]], symbol: str) -> Decimal:
    min_notional_filter = filters.get("MIN_NOTIONAL")
    if min_notional_filter is None:
        _log_rule_warning(symbol, "missing_MIN_NOTIONAL", fallback=DEFAULT_MIN_NOTIONAL)
        min_notional_filter = {}

    return _decimal(
        min_notional_filter.get("notional", min_notional_filter.get("minNotional")),
        DEFAULT_MIN_NOTIONAL,
        symbol=symbol,
        field="MIN_NOTIONAL_notional",
    )


def parse_futures_symbol_rules(symbol_info: dict[str, Any]) -> FuturesSymbolRules:
    symbol = str(symbol_info.get("symbol", "")).upper()
    if not symbol:
        raise ValueError("Futures symbol info is missing symbol")

    filters = _filters_by_type(symbol_info)
    price_filter = filters.get("PRICE_FILTER") or {}
    lot_size = filters.get("LOT_SIZE") or {}
    market_lot_size = filters.get("MARKET_LOT_SIZE") or {}

    tick_size = _decimal(
        price_filter.get("tickSize"),
        DEFAULT_TICK_SIZE,
        symbol=symbol,
        field="PRICE_FILTER_tickSize",
    )
    step_size = _decimal(
        lot_size.get("stepSize"),
        DEFAULT_STEP_SIZE,
        symbol=symbol,
        field="LOT_SIZE_stepSize",
    )
    min_notional = _parse_min_notional(filters, symbol)

    return FuturesSymbolRules(
        symbol=symbol,
        status=str(symbol_info.get("status") or DEFAULT_STATUS),
        contract_type=str(symbol_info.get("contractType") or DEFAULT_CONTRACT_TYPE),
        price_precision=_integer(
            symbol_info.get("pricePrecision"),
            DEFAULT_PRICE_PRECISION,
            symbol=symbol,
            field="pricePrecision",
        ),
        quantity_precision=_integer(
            symbol_info.get("quantityPrecision"),
            DEFAULT_QUANTITY_PRECISION,
            symbol=symbol,
            field="quantityPrecision",
        ),
        tick_size=float(tick_size),
        step_size=float(step_size),
        min_qty=float(_decimal(
            lot_size.get("minQty"),
            DEFAULT_MIN_QTY,
            symbol=symbol,
            field="LOT_SIZE_minQty",
        )),
        max_qty=float(_decimal(
            lot_size.get("maxQty"),
            DEFAULT_MAX_QTY,
            symbol=symbol,
            field="LOT_SIZE_maxQty",
        )),
        min_notional=float(min_notional),
        market_min_qty=float(_decimal(
            market_lot_size.get("minQty"),
            DEFAULT_MARKET_MIN_QTY,
            symbol=symbol,
            field="MARKET_LOT_SIZE_minQty",
        )),
        market_max_qty=float(_decimal(
            market_lot_size.get("maxQty"),
            DEFAULT_MARKET_MAX_QTY,
            symbol=symbol,
            field="MARKET_LOT_SIZE_maxQty",
        )),
        market_step_size=float(_decimal(
            market_lot_size.get("stepSize"),
            DEFAULT_MARKET_STEP_SIZE,
            symbol=symbol,
            field="MARKET_LOT_SIZE_stepSize",
        )),
    )


def get_default_futures_symbol_rules(symbol: str) -> FuturesSymbolRules:
    return FuturesSymbolRules(
        symbol=symbol.upper(),
        status=DEFAULT_STATUS,
        contract_type=DEFAULT_CONTRACT_TYPE,
        price_precision=DEFAULT_PRICE_PRECISION,
        quantity_precision=DEFAULT_QUANTITY_PRECISION,
        tick_size=float(DEFAULT_TICK_SIZE),
        step_size=float(DEFAULT_STEP_SIZE),
        min_qty=float(DEFAULT_MIN_QTY),
        max_qty=float(DEFAULT_MAX_QTY),
        min_notional=float(DEFAULT_MIN_NOTIONAL),
        market_min_qty=float(DEFAULT_MARKET_MIN_QTY),
        market_max_qty=float(DEFAULT_MARKET_MAX_QTY),
        market_step_size=float(DEFAULT_MARKET_STEP_SIZE),
    )


def _rules_cache_ttl_seconds() -> int:
    try:
        ttl_seconds = int(load_futures_config().futures.rules_cache_ttl_seconds)
    except Exception as exc:
        _log_rule_warning(
            "-",
            "futures_rules_cache_ttl_unavailable_using_default",
            error=str(exc),
            fallback=DEFAULT_RULES_CACHE_TTL_SECONDS,
        )
        ttl_seconds = DEFAULT_RULES_CACHE_TTL_SECONDS
    return max(0, ttl_seconds)


def _is_cache_valid(cache_entry: CachedFuturesSymbolRules, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    age_seconds = (datetime.now(timezone.utc) - cache_entry.fetched_at).total_seconds()
    return age_seconds < ttl_seconds


def _build_client() -> BinanceFuturesClient:
    return BinanceFuturesClient()


def fetch_futures_symbol_rules(symbol: str) -> FuturesSymbolRules:
    symbol = symbol.upper()
    cached = _RULES_CACHE.get(symbol)
    ttl_seconds = _rules_cache_ttl_seconds()
    if cached is not None and _is_cache_valid(cached, ttl_seconds):
        return cached.rules

    try:
        symbol_info = _build_client().get_symbol_info(symbol)
        rules = parse_futures_symbol_rules(symbol_info)
        _RULES_CACHE[symbol] = CachedFuturesSymbolRules(
            rules=rules,
            fetched_at=datetime.now(timezone.utc),
        )
        return rules
    except Exception as exc:
        if cached is not None:
            _log_rule_warning(
                symbol,
                "fetch_futures_symbol_rules_failed_using_stale_cache",
                error=str(exc),
                fetched_at=cached.fetched_at.isoformat(),
            )
            return cached.rules

        _log_rule_warning(
            symbol,
            "fetch_futures_symbol_rules_failed_using_local_defaults",
            error=str(exc),
        )
        return get_default_futures_symbol_rules(symbol)


def _round_down_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def normalize_futures_price(symbol: str, price: float) -> float:
    rules = fetch_futures_symbol_rules(symbol)
    normalized = _round_down_to_step(
        Decimal(str(price)),
        Decimal(str(rules.tick_size)),
    )
    return float(normalized)


def normalize_futures_quantity(symbol: str, quantity: float) -> float:
    rules = fetch_futures_symbol_rules(symbol)
    normalized = _round_down_to_step(
        Decimal(str(quantity)),
        Decimal(str(rules.step_size)),
    )
    return float(normalized)


def validate_futures_notional(symbol: str, price: float, quantity: float) -> bool:
    rules = fetch_futures_symbol_rules(symbol)
    notional = Decimal(str(price)) * Decimal(str(quantity))
    return notional >= Decimal(str(rules.min_notional))
