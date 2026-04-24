from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from config.loader import load_execution_runtime, load_project_config
from exchange.binance_client import BinanceClient
from observability.event_logger import StructuredLogger


DEFAULT_MIN_NOTIONAL = 5.0
DEFAULT_AMOUNT_PRECISION = 2
DEFAULT_QUANTITY_PRECISION = 6
DEFAULT_TICK_SIZE = "0.01"
DEFAULT_MIN_QTY = "0.000001"
DEFAULT_MAX_QTY = "100000000"
DEFAULT_STEP_SIZE = "0.000001"
DEFAULT_ERROR_LOG_FILE = "logs/error.log"
DEFAULT_RULES_CACHE_TTL_SECONDS = 3600


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    tick_size: float = float(DEFAULT_TICK_SIZE)
    lot_size_min_qty: float = float(DEFAULT_MIN_QTY)
    lot_size_max_qty: float = float(DEFAULT_MAX_QTY)
    lot_size_step_size: float = float(DEFAULT_STEP_SIZE)
    market_lot_size_min_qty: float = float(DEFAULT_MIN_QTY)
    market_lot_size_max_qty: float = float(DEFAULT_MAX_QTY)
    market_lot_size_step_size: float = float(DEFAULT_STEP_SIZE)
    min_notional: float = DEFAULT_MIN_NOTIONAL
    notional_min: float | None = None
    notional_max: float | None = None
    amount_precision: int = DEFAULT_AMOUNT_PRECISION
    quantity_precision: int = DEFAULT_QUANTITY_PRECISION


@dataclass(frozen=True)
class CachedSymbolRules:
    rules: SymbolRules
    fetched_at: datetime


SymbolTradingRules = SymbolRules
_RULES_CACHE: dict[str, CachedSymbolRules] = {}


def _logger() -> StructuredLogger:
    try:
        settings = load_project_config()
        error_log_file = str(settings.get("logging", {}).get("error_log_file", DEFAULT_ERROR_LOG_FILE))
    except Exception:
        error_log_file = DEFAULT_ERROR_LOG_FILE
    return StructuredLogger(error_log_file)


def _log_rule_warning(symbol: str, reason: str, **extra: Any) -> None:
    _logger().log(
        symbol=symbol.upper(),
        action="binance_rule_fallback",
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


def _precision_from_step(step: Decimal, fallback: int) -> int:
    if step <= 0:
        return fallback
    normalized = step.normalize()
    return max(0, -normalized.as_tuple().exponent)


def _filters_by_type(symbol_info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    filters = symbol_info.get("filters", [])
    if not isinstance(filters, list):
        return {}
    return {
        str(item.get("filterType")): item
        for item in filters
        if isinstance(item, dict) and item.get("filterType")
    }


def _parse_symbol_rules(symbol: str, symbol_info: dict[str, Any]) -> SymbolRules:
    symbol = symbol.upper()
    filters = _filters_by_type(symbol_info)

    price_filter = filters.get("PRICE_FILTER")
    if price_filter is None:
        _log_rule_warning(symbol, "missing_PRICE_FILTER", fallback_tick_size=DEFAULT_TICK_SIZE)
        price_filter = {}

    lot_size = filters.get("LOT_SIZE")
    if lot_size is None:
        _log_rule_warning(symbol, "missing_LOT_SIZE", fallback_step_size=DEFAULT_STEP_SIZE)
        lot_size = {}

    market_lot_size = filters.get("MARKET_LOT_SIZE")
    if market_lot_size is None:
        _log_rule_warning(symbol, "missing_MARKET_LOT_SIZE", fallback_step_size=DEFAULT_STEP_SIZE)
        market_lot_size = {}

    min_notional_filter = filters.get("MIN_NOTIONAL")
    notional_filter = filters.get("NOTIONAL")
    if min_notional_filter is None and notional_filter is None:
        _log_rule_warning(symbol, "missing_MIN_NOTIONAL_and_NOTIONAL", fallback=DEFAULT_MIN_NOTIONAL)
        min_notional_filter = {}
    elif min_notional_filter is None:
        min_notional_filter = {}
    elif notional_filter is None:
        notional_filter = {}

    tick_size = _decimal(
        price_filter.get("tickSize"),
        DEFAULT_TICK_SIZE,
        symbol=symbol,
        field="PRICE_FILTER_tickSize",
    )
    lot_step_size = _decimal(
        lot_size.get("stepSize"),
        DEFAULT_STEP_SIZE,
        symbol=symbol,
        field="LOT_SIZE_stepSize",
    )
    min_notional = _decimal(
        (notional_filter or {}).get("minNotional", min_notional_filter.get("minNotional")),
        str(DEFAULT_MIN_NOTIONAL),
        symbol=symbol,
        field="minNotional",
    )

    return SymbolRules(
        symbol=symbol,
        tick_size=float(tick_size),
        lot_size_min_qty=float(_decimal(
            lot_size.get("minQty"),
            DEFAULT_MIN_QTY,
            symbol=symbol,
            field="LOT_SIZE_minQty",
        )),
        lot_size_max_qty=float(_decimal(
            lot_size.get("maxQty"),
            DEFAULT_MAX_QTY,
            symbol=symbol,
            field="LOT_SIZE_maxQty",
        )),
        lot_size_step_size=float(lot_step_size),
        market_lot_size_min_qty=float(_decimal(
            market_lot_size.get("minQty"),
            DEFAULT_MIN_QTY,
            symbol=symbol,
            field="MARKET_LOT_SIZE_minQty",
        )),
        market_lot_size_max_qty=float(_decimal(
            market_lot_size.get("maxQty"),
            DEFAULT_MAX_QTY,
            symbol=symbol,
            field="MARKET_LOT_SIZE_maxQty",
        )),
        market_lot_size_step_size=float(_decimal(
            market_lot_size.get("stepSize"),
            DEFAULT_STEP_SIZE,
            symbol=symbol,
            field="MARKET_LOT_SIZE_stepSize",
        )),
        min_notional=float(min_notional),
        notional_min=(
            float(_decimal(
                notional_filter.get("minNotional"),
                str(min_notional),
                symbol=symbol,
                field="NOTIONAL_minNotional",
            ))
            if notional_filter and notional_filter.get("minNotional") not in (None, "")
            else None
        ),
        notional_max=(
            float(_decimal(
                notional_filter.get("maxNotional"),
                "0",
                symbol=symbol,
                field="NOTIONAL_maxNotional",
            ))
            if notional_filter and notional_filter.get("maxNotional") not in (None, "")
            else None
        ),
        amount_precision=_precision_from_step(tick_size, DEFAULT_AMOUNT_PRECISION),
        quantity_precision=_precision_from_step(lot_step_size, DEFAULT_QUANTITY_PRECISION),
    )


def get_default_symbol_rules(symbol: str) -> SymbolRules:
    return SymbolRules(symbol=symbol.upper())


def _build_client() -> BinanceClient:
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    return BinanceClient(
        base_url=execution_config.exchange.base_url,
        timeout=execution_config.exchange.request_timeout_seconds,
        error_log_file=execution_config.error_log_file,
    )


def _rules_cache_ttl_seconds() -> int:
    try:
        settings = load_project_config()
        execution_config = load_execution_runtime(settings)
        ttl_seconds = int(execution_config.binance.rules_cache_ttl_seconds)
    except Exception:
        ttl_seconds = DEFAULT_RULES_CACHE_TTL_SECONDS
    return max(0, ttl_seconds)


def _is_cache_valid(cache_entry: CachedSymbolRules, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    age_seconds = (datetime.now(timezone.utc) - cache_entry.fetched_at).total_seconds()
    return age_seconds < ttl_seconds


def fetch_symbol_rules(symbol: str) -> SymbolRules:
    symbol = symbol.upper()
    cached = _RULES_CACHE.get(symbol)
    ttl_seconds = _rules_cache_ttl_seconds()
    if cached is not None and _is_cache_valid(cached, ttl_seconds):
        return cached.rules

    try:
        symbol_info = _build_client().get_symbol_info(symbol)
        rules = _parse_symbol_rules(symbol, symbol_info)
        _RULES_CACHE[symbol] = CachedSymbolRules(
            rules=rules,
            fetched_at=datetime.now(timezone.utc),
        )
        return rules
    except Exception as exc:
        if cached is not None:
            _log_rule_warning(
                symbol,
                "fetch_symbol_rules_failed_using_stale_cache",
                error=str(exc),
                fetched_at=cached.fetched_at.isoformat(),
            )
            return cached.rules

        _log_rule_warning(symbol, "fetch_symbol_rules_failed_using_local_defaults", error=str(exc))
        return get_default_symbol_rules(symbol)


def _round_down_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def normalize_price(symbol: str, price: float) -> float:
    rules = fetch_symbol_rules(symbol)
    normalized = _round_down_to_step(Decimal(str(price)), Decimal(str(rules.tick_size)))
    return float(normalized)


def normalize_quantity(symbol: str, quantity: float) -> float:
    rules = fetch_symbol_rules(symbol)
    normalized = _round_down_to_step(Decimal(str(quantity)), Decimal(str(rules.lot_size_step_size)))
    return float(normalized)


def validate_notional(symbol: str, price: float, quantity: float) -> bool:
    rules = fetch_symbol_rules(symbol)
    notional = Decimal(str(price)) * Decimal(str(quantity))
    min_notional = Decimal(str(rules.notional_min or rules.min_notional))
    if notional < min_notional:
        return False
    if (
        rules.notional_max is not None
        and rules.notional_max > 0
        and notional > Decimal(str(rules.notional_max))
    ):
        return False
    return True
