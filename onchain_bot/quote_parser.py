from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _decimal_from_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _format_decimal(value: Decimal | None, *, max_places: int = 12) -> str | None:
    if value is None:
        return None
    normalized = value.quantize(Decimal(1)) if value == value.to_integral() else value.normalize()
    text = format(normalized, "f")
    if "." in text:
        whole, fraction = text.split(".", 1)
        fraction = fraction[:max_places].rstrip("0")
        return whole if not fraction else f"{whole}.{fraction}"
    return text


def _base_units_to_decimal(value: Any, decimals: int) -> Decimal | None:
    raw = _decimal_from_value(value)
    if raw is None:
        return None
    return raw / (Decimal(10) ** decimals)


def _first_quote_item(raw_quote: Any) -> dict[str, Any]:
    if not isinstance(raw_quote, dict):
        return {}
    data = raw_quote.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        return data
    return raw_quote


def _pick(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _extract_route(item: dict[str, Any]) -> str | None:
    route_parts: list[str] = []
    routers = item.get("dexRouterList")
    if not isinstance(routers, list):
        routers = item.get("routerResult")
    if not isinstance(routers, list):
        return None
    for router in routers:
        if not isinstance(router, dict):
            continue
        router_name = router.get("router") or router.get("dexName")
        sub_routers = router.get("subRouterList")
        dex_names: list[str] = []
        if isinstance(sub_routers, list):
            for sub_router in sub_routers:
                if not isinstance(sub_router, dict):
                    continue
                protocols = sub_router.get("dexProtocol")
                if isinstance(protocols, list):
                    for protocol in protocols:
                        if isinstance(protocol, dict) and protocol.get("dexName"):
                            dex_names.append(str(protocol["dexName"]))
        if router_name:
            route_parts.append(str(router_name))
        route_parts.extend(dex_names)
    deduped = list(dict.fromkeys(route_parts))
    return " → ".join(deduped) if deduped else None


def _extract_gas_usdt(item: dict[str, Any]) -> str | None:
    gas_value = _pick(
        item,
        (
            "estimateGasFee",
            "estimatedGasFee",
            "gasFee",
            "gasUsd",
            "estimatedGasUsd",
        ),
    )
    gas_decimal = _decimal_from_value(gas_value)
    return _format_decimal(gas_decimal) if gas_decimal is not None else None


def parse_okx_quote(
    *,
    raw_quote: Any,
    amount_usdt: float | int | str | None,
    quote_token_symbol: str,
    quote_token_decimals: int,
    token_symbol: str,
    token_decimals: int,
    max_slippage_pct: float | int | str | None = None,
    latency_ms: float | int | None = None,
) -> dict[str, Any]:
    item = _first_quote_item(raw_quote)
    from_amount_raw = _pick(item, ("fromTokenAmount", "fromTokenAmountMin", "amountIn"))
    to_amount_raw = _pick(item, ("toTokenAmount", "toTokenAmountMin", "amountOut"))

    from_amount = _base_units_to_decimal(from_amount_raw, quote_token_decimals)
    if from_amount is None:
        from_amount = _decimal_from_value(amount_usdt)
    to_amount = _base_units_to_decimal(to_amount_raw, token_decimals)

    implied_price = None
    if from_amount is not None and to_amount is not None and to_amount > 0:
        implied_price = from_amount / to_amount

    price_impact = _pick(
        item,
        (
            "priceImpactPercentage",
            "priceImpactPct",
            "priceImpact",
        ),
    )

    from_amount_text = _format_decimal(from_amount)
    to_amount_text = _format_decimal(to_amount)
    implied_price_text = _format_decimal(implied_price)
    max_slippage_text = _format_decimal(_decimal_from_value(max_slippage_pct), max_places=6)

    return {
        "from_amount_display": f"{from_amount_text} {quote_token_symbol}" if from_amount_text else None,
        "to_amount_display": f"{to_amount_text} {token_symbol}" if to_amount_text else None,
        "implied_price": (
            f"{implied_price_text} {quote_token_symbol}/{token_symbol}"
            if implied_price_text
            else None
        ),
        "max_slippage_pct": f"{max_slippage_text}%" if max_slippage_text is not None else None,
        "estimated_gas_usdt": _extract_gas_usdt(item),
        "price_impact_pct": f"{price_impact}%" if price_impact not in (None, "") else None,
        "route": _extract_route(item),
        "latency_ms": latency_ms,
    }
