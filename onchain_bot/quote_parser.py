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
        direct_protocols = router.get("dexProtocol")
        sub_routers = router.get("subRouterList")
        dex_names: list[str] = []
        percent = router.get("percent") or router.get("percentage") or router.get("ratio")
        if isinstance(direct_protocols, dict) and direct_protocols.get("dexName"):
            percent = percent or direct_protocols.get("percent") or direct_protocols.get("percentage") or direct_protocols.get("ratio")
            dex_name = str(direct_protocols["dexName"])
            dex_names.append(f"{dex_name} {percent}%" if percent not in (None, "") else dex_name)
        elif isinstance(direct_protocols, list):
            for protocol in direct_protocols:
                if isinstance(protocol, dict) and protocol.get("dexName"):
                    protocol_percent = percent or protocol.get("percent") or protocol.get("percentage") or protocol.get("ratio")
                    dex_name = str(protocol["dexName"])
                    dex_names.append(f"{dex_name} {protocol_percent}%" if protocol_percent not in (None, "") else dex_name)
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


def _extract_estimated_gas_raw(item: dict[str, Any]) -> str | None:
    gas_value = _pick(item, ("estimateGasFee", "estimatedGasFee", "gasFee"))
    gas_decimal = _decimal_from_value(gas_value)
    return _format_decimal(gas_decimal) if gas_decimal is not None else None


def _extract_estimated_gas_usdt(item: dict[str, Any]) -> str | None:
    gas_value = _pick(item, ("gasUsd", "estimatedGasUsd", "gasUsdt", "estimatedGasUsdt"))
    gas_decimal = _decimal_from_value(gas_value)
    return _format_decimal(gas_decimal) if gas_decimal is not None else None


def parse_okx_quote(
    *,
    raw_quote: Any,
    amount_display: float | int | str | None = None,
    from_token_symbol: str | None = None,
    from_token_decimals: int | None = None,
    to_token_symbol: str | None = None,
    to_token_decimals: int | None = None,
    direction: str = "buy",
    amount_usdt: float | int | str | None = None,
    quote_token_symbol: str | None = None,
    quote_token_decimals: int | None = None,
    token_symbol: str | None = None,
    token_decimals: int | None = None,
    max_slippage_pct: float | int | str | None = None,
    latency_ms: float | int | None = None,
) -> dict[str, Any]:
    if from_token_symbol is None:
        from_token_symbol = quote_token_symbol
    if from_token_decimals is None:
        from_token_decimals = quote_token_decimals
    if to_token_symbol is None:
        to_token_symbol = token_symbol
    if to_token_decimals is None:
        to_token_decimals = token_decimals
    if amount_display is None:
        amount_display = amount_usdt
    from_token_symbol = from_token_symbol or "FROM"
    to_token_symbol = to_token_symbol or "TO"
    from_token_decimals = int(from_token_decimals or 0)
    to_token_decimals = int(to_token_decimals or 0)

    item = _first_quote_item(raw_quote)
    from_amount_raw = _pick(item, ("fromTokenAmount", "fromTokenAmountMin", "amountIn"))
    to_amount_raw = _pick(item, ("toTokenAmount", "toTokenAmountMin", "amountOut"))

    from_amount = _base_units_to_decimal(from_amount_raw, from_token_decimals)
    if from_amount is None:
        from_amount = _decimal_from_value(amount_display)
    to_amount = _base_units_to_decimal(to_amount_raw, to_token_decimals)

    implied_price = None
    if from_amount is not None and to_amount is not None and to_amount > 0:
        implied_price = from_amount / to_amount

    price_impact = _pick(
        item,
        (
            "priceImpactPercent",
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
        "direction": direction,
        "from_token_symbol": from_token_symbol,
        "to_token_symbol": to_token_symbol,
        "from_amount_display": f"{from_amount_text} {from_token_symbol}" if from_amount_text else None,
        "to_amount_display": f"{to_amount_text} {to_token_symbol}" if to_amount_text else None,
        "implied_price": (
            f"{implied_price_text} {from_token_symbol}/{to_token_symbol}"
            if implied_price_text
            else None
        ),
        "max_slippage_pct": f"{max_slippage_text}%" if max_slippage_text is not None else None,
        "estimated_gas_raw": _extract_estimated_gas_raw(item),
        "estimated_gas_usdt": _extract_estimated_gas_usdt(item),
        "price_impact_pct": f"{price_impact}%" if price_impact not in (None, "") else None,
        "route": _extract_route(item),
        "latency_ms": latency_ms,
    }
