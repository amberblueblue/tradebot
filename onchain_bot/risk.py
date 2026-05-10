from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from onchain_bot.config_loader import load_onchain_settings_config


DEFAULT_RISK = {
    "max_price_impact_pct": 1.0,
    "max_slippage_pct": 1.0,
    "max_gas_raw": 500000.0,
    "quote_stale_seconds": 600,
    "max_token_tax_rate_pct": 0.0,
}


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    text = str(value).strip().replace("%", "").split(" ", 1)[0]
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _quote_item(quote_result: dict[str, Any]) -> dict[str, Any]:
    quote = quote_result.get("quote")
    if not isinstance(quote, dict):
        return {}
    data = quote.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return data if isinstance(data, dict) else quote


def _tokens(item: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for key in ("fromToken", "toToken"):
        token = item.get(key)
        if isinstance(token, dict):
            found.append(token)
    return found


def _quote_is_stale(quote_result: dict[str, Any], *, ttl_seconds: int) -> bool:
    quoted_at = quote_result.get("quoted_at")
    if not isinstance(quoted_at, str) or not quoted_at:
        return True
    try:
        timestamp = datetime.fromisoformat(quoted_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds() > ttl_seconds


def effective_onchain_risk(mapping: Any) -> dict[str, float]:
    try:
        settings = load_onchain_settings_config()
        global_risk = {
            "max_price_impact_pct": settings.risk_max_price_impact_pct,
            "max_slippage_pct": settings.risk_max_slippage_pct,
            "max_gas_raw": settings.risk_max_gas_raw,
            "quote_stale_seconds": float(settings.risk_quote_stale_seconds),
            "max_token_tax_rate_pct": settings.risk_max_token_tax_rate_pct,
        }
    except Exception:
        global_risk = {}

    risk = {**DEFAULT_RISK, **global_risk}
    symbol_risk = getattr(mapping, "risk", None)
    if isinstance(symbol_risk, dict):
        for key, value in symbol_risk.items():
            if key in risk and value is not None:
                risk[key] = float(value)
    return risk


def check_onchain_quote_risk(
    symbol: str,
    mapping: Any,
    quote_result: dict[str, Any] | None,
    direction: str,
) -> dict[str, Any]:
    failures: list[str] = []
    details: dict[str, Any] = {
        "symbol": symbol,
        "direction": direction,
    }
    risk = effective_onchain_risk(mapping)
    details["limits"] = risk

    if quote_result is None:
        return {
            "ok": False,
            "reason": "quote_not_available",
            "failures": ["quote_not_available"],
            "details": details,
        }
    if not bool(quote_result.get("ok")):
        failures.append("invalid_quote")
    if _quote_is_stale(quote_result, ttl_seconds=int(risk["quote_stale_seconds"])):
        failures.append("quote_stale")

    parsed_quote = quote_result.get("parsed_quote")
    parsed_quote = parsed_quote if isinstance(parsed_quote, dict) else {}
    price_impact = _decimal(parsed_quote.get("price_impact_pct") or quote_result.get("price_impact_pct"))
    gas_raw = _decimal(parsed_quote.get("estimated_gas_raw"))
    slippage = _decimal(parsed_quote.get("max_slippage_pct"))

    details["price_impact_pct"] = float(price_impact) if price_impact is not None else None
    details["gas_raw"] = float(gas_raw) if gas_raw is not None else None
    details["slippage_pct"] = float(slippage) if slippage is not None else None

    if price_impact is not None and abs(price_impact) > Decimal(str(risk["max_price_impact_pct"])):
        failures.append("price_impact_too_high")
    if slippage is not None and slippage > Decimal(str(risk["max_slippage_pct"])):
        failures.append("slippage_too_high")
    if gas_raw is not None and gas_raw > Decimal(str(risk["max_gas_raw"])):
        failures.append("gas_too_high")

    item = _quote_item(quote_result)
    max_tax_rate = Decimal(str(risk["max_token_tax_rate_pct"]))
    tax_rates: list[float] = []
    honey_pot = False
    for token in _tokens(item):
        honey_pot = honey_pot or bool(token.get("isHoneyPot"))
        tax_rate = _decimal(token.get("taxRate"))
        if tax_rate is not None:
            tax_rates.append(float(tax_rate))
            if tax_rate > max_tax_rate:
                failures.append("token_tax_too_high")
    if honey_pot:
        failures.append("token_is_honeypot")

    details["tax_rates"] = tax_rates
    details["is_honeypot"] = honey_pot
    failures = list(dict.fromkeys(failures))
    return {
        "ok": not failures,
        "reason": "ok" if not failures else failures[0],
        "failures": failures,
        "details": details,
    }
