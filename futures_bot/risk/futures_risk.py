from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from futures_bot.config_loader import load_futures_config


@dataclass(frozen=True)
class FuturesRiskResult:
    ok: bool
    reason: str
    symbol: str
    leverage: float
    margin_amount: float
    funding_rate: float
    liquidation_distance_pct: float | None
    position_ratio: float | None
    details: dict[str, Any] = field(default_factory=dict)


def _as_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number")
    return float(value)


def _liquidation_distance_pct(mark_price: float, liquidation_price: float | None) -> float | None:
    if liquidation_price is None:
        return None
    if mark_price <= 0:
        raise ValueError("mark_price must be greater than 0 when liquidation_price is provided")
    return abs(mark_price - liquidation_price) / mark_price * 100


def _error_result(
    *,
    reason: str,
    symbol: str,
    leverage: float,
    margin_amount: float,
    funding_rate: float,
    liquidation_distance_pct: float | None = None,
    position_ratio: float | None = None,
    details: dict[str, Any] | None = None,
) -> FuturesRiskResult:
    return FuturesRiskResult(
        ok=False,
        reason=reason,
        symbol=symbol,
        leverage=leverage,
        margin_amount=margin_amount,
        funding_rate=funding_rate,
        liquidation_distance_pct=liquidation_distance_pct,
        position_ratio=position_ratio,
        details=details or {},
    )


def check_futures_pre_open_risk(
    symbol,
    side,
    margin_amount,
    leverage,
    mark_price,
    funding_rate,
    account_equity,
    liquidation_price=None,
    existing_position_notional=0,
):
    """Dry-run pre-open risk skeleton; never places orders or changes exchange state."""
    normalized_symbol = str(symbol).upper()
    normalized_side = str(side).upper()
    liquidation_distance = None
    position_ratio = None

    try:
        config = load_futures_config()
        risk_config = config.risk

        margin_amount_value = _as_float(margin_amount, "margin_amount")
        leverage_value = _as_float(leverage, "leverage")
        mark_price_value = _as_float(mark_price, "mark_price")
        funding_rate_value = _as_float(funding_rate, "funding_rate")
        account_equity_value = _as_float(account_equity, "account_equity")
        existing_position_notional_value = _as_float(
            existing_position_notional,
            "existing_position_notional",
        )
        liquidation_price_value = (
            None
            if liquidation_price is None
            else _as_float(liquidation_price, "liquidation_price")
        )

        if margin_amount_value <= 0:
            return _error_result(
                reason="invalid_margin_amount",
                symbol=normalized_symbol,
                leverage=leverage_value,
                margin_amount=margin_amount_value,
                funding_rate=funding_rate_value,
                details={"side": normalized_side},
            )
        if leverage_value <= 0:
            return _error_result(
                reason="invalid_leverage",
                symbol=normalized_symbol,
                leverage=leverage_value,
                margin_amount=margin_amount_value,
                funding_rate=funding_rate_value,
                details={"side": normalized_side},
            )
        if account_equity_value <= 0:
            return _error_result(
                reason="invalid_account_equity",
                symbol=normalized_symbol,
                leverage=leverage_value,
                margin_amount=margin_amount_value,
                funding_rate=funding_rate_value,
                details={"side": normalized_side, "account_equity": account_equity_value},
            )

        new_position_notional = margin_amount_value * leverage_value
        total_position_notional = existing_position_notional_value + new_position_notional
        position_ratio = total_position_notional / account_equity_value
        liquidation_distance = _liquidation_distance_pct(
            mark_price_value,
            liquidation_price_value,
        )

        failures: list[str] = []
        if leverage_value > risk_config.max_leverage:
            failures.append("leverage_exceeds_max")
        if margin_amount_value > risk_config.max_margin_per_trade_usdt:
            failures.append("margin_amount_exceeds_max")
        if position_ratio > risk_config.max_position_ratio:
            failures.append("position_ratio_exceeds_max")
        if abs(funding_rate_value) > risk_config.max_funding_rate_abs:
            failures.append("funding_rate_exceeds_max_abs")
        if (
            liquidation_distance is not None
            and liquidation_distance < risk_config.min_liquidation_distance_pct
        ):
            failures.append("liquidation_distance_too_close")

        details = {
            "side": normalized_side,
            "new_position_notional": new_position_notional,
            "existing_position_notional": existing_position_notional_value,
            "total_position_notional": total_position_notional,
            "account_equity": account_equity_value,
            "limits": {
                "max_leverage": risk_config.max_leverage,
                "max_margin_per_trade_usdt": risk_config.max_margin_per_trade_usdt,
                "max_position_ratio": risk_config.max_position_ratio,
                "min_liquidation_distance_pct": risk_config.min_liquidation_distance_pct,
                "max_funding_rate_abs": risk_config.max_funding_rate_abs,
            },
            "failures": failures,
        }

        return FuturesRiskResult(
            ok=not failures,
            reason="ok" if not failures else failures[0],
            symbol=normalized_symbol,
            leverage=leverage_value,
            margin_amount=margin_amount_value,
            funding_rate=funding_rate_value,
            liquidation_distance_pct=liquidation_distance,
            position_ratio=position_ratio,
            details=details,
        )
    except Exception as exc:
        return _error_result(
            reason="risk_check_error",
            symbol=normalized_symbol,
            leverage=float(leverage) if isinstance(leverage, (int, float)) else 0.0,
            margin_amount=float(margin_amount) if isinstance(margin_amount, (int, float)) else 0.0,
            funding_rate=float(funding_rate) if isinstance(funding_rate, (int, float)) else 0.0,
            liquidation_distance_pct=liquidation_distance,
            position_ratio=position_ratio,
            details={"error": str(exc), "side": normalized_side},
        )
