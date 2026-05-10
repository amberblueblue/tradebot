from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from futures_bot.config_loader import load_yaml_mapping
from onchain_bot.session_filter import ALLOWED_EXECUTION_SESSION_FILTERS


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_ONCHAIN_SYMBOLS_PATH = CONFIG_DIR / "onchain_symbols.yaml"
DEFAULT_ONCHAIN_SETTINGS_PATH = CONFIG_DIR / "onchain_settings.yaml"


@dataclass(frozen=True)
class OnchainSettings:
    app_mode: str
    polling_interval_seconds: int
    quote_auto_refresh_enabled: bool
    quote_stale_seconds: int
    quote_default_amount_usdt: float
    live_auto_live_enabled: bool
    live_default_order_amount_usdt: float
    live_require_manual_confirm_env: bool
    live_wallet_signing_enabled: bool
    live_broadcast_enabled: bool
    live_require_wallet_env: bool
    live_max_live_order_usdt: float
    live_max_live_trades_per_day: int
    safety_allow_live_trading: bool
    safety_live_execute_enabled: bool
    risk_max_price_impact_pct: float
    risk_max_slippage_pct: float
    risk_max_gas_raw: float
    risk_quote_stale_seconds: int
    risk_max_token_tax_rate_pct: float
    risk_max_trade_usdt: float
    risk_max_live_order_usdt: float
    risk_max_live_trades_per_day: int
    risk_max_open_positions: int
    risk_max_opens_per_day: int
    risk_max_closes_per_day: int
    risk_min_trade_interval_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OnchainSymbolConfig:
    symbol: str
    enabled: bool
    signal_source: str
    source_symbol: str
    execution_session_filter: str
    chain_name: str
    chain_id: str
    token_symbol: str
    token_name: str
    token_address: str
    token_decimals: int
    quote_token_symbol: str
    quote_token_address: str
    quote_token_decimals: int
    max_trade_usdt: float
    max_slippage_pct: float
    max_gas_usdt: float
    risk: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _require_string(raw: dict[str, Any], key: str, symbol: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"symbols.{symbol}.{key} must be a non-empty string")
    return value


def _require_bool(raw: dict[str, Any], key: str, symbol: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"symbols.{symbol}.{key} must be boolean")
    return value


def _require_positive_int(raw: dict[str, Any], key: str, symbol: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"symbols.{symbol}.{key} must be an integer greater than 0")
    if value <= 0:
        raise ValueError(f"symbols.{symbol}.{key} must be greater than 0")
    return value


def _require_positive_number(raw: dict[str, Any], key: str, symbol: str) -> float:
    value = raw.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"symbols.{symbol}.{key} must be a number greater than 0")
    if value <= 0:
        raise ValueError(f"symbols.{symbol}.{key} must be greater than 0")
    return float(value)


def _require_non_negative_number(raw: dict[str, Any], key: str, symbol: str) -> float:
    value = raw.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"symbols.{symbol}.{key} must be a number greater than or equal to 0")
    if value < 0:
        raise ValueError(f"symbols.{symbol}.{key} must be greater than or equal to 0")
    return float(value)


def _validate_upper(value: str, field_name: str, symbol: str) -> str:
    if value != value.upper():
        raise ValueError(f"symbols.{symbol}.{field_name} must be uppercase")
    return value


def _validate_address(value: str, field_name: str, symbol: str) -> str:
    if not value.startswith("0x") or len(value) != 42:
        raise ValueError(f"symbols.{symbol}.{field_name} must be a 0x-prefixed 42-character address")
    hex_part = value[2:]
    if any(char not in "0123456789abcdefABCDEF" for char in hex_part):
        raise ValueError(f"symbols.{symbol}.{field_name} must be a hex address")
    return value


def _optional_execution_session_filter(raw: dict[str, Any], symbol: str) -> str:
    value = raw.get("execution_session_filter", "us_regular")
    if not isinstance(value, str) or not value:
        raise ValueError(f"symbols.{symbol}.execution_session_filter must be a non-empty string")
    normalized = value.strip().lower()
    if normalized not in ALLOWED_EXECUTION_SESSION_FILTERS:
        allowed = ", ".join(ALLOWED_EXECUTION_SESSION_FILTERS)
        raise ValueError(f"symbols.{symbol}.execution_session_filter must be one of: {allowed}")
    return normalized


RISK_KEYS = (
    "max_price_impact_pct",
    "max_slippage_pct",
    "max_gas_raw",
    "quote_stale_seconds",
    "max_token_tax_rate_pct",
    "max_trade_usdt",
    "min_trade_interval_seconds",
)


def _optional_risk_overrides(raw: dict[str, Any], symbol: str) -> dict[str, float]:
    risk = raw.get("risk")
    if risk is None:
        return {}
    risk_mapping = _require_mapping(risk, f"symbols.{symbol}.risk")
    parsed: dict[str, float] = {}
    for key in RISK_KEYS:
        if key not in risk_mapping or risk_mapping[key] in (None, ""):
            continue
        value = risk_mapping[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"symbols.{symbol}.risk.{key} must be a number")
        if key == "max_trade_usdt" and value <= 0:
            raise ValueError(f"symbols.{symbol}.risk.{key} must be greater than 0")
        if value < 0:
            raise ValueError(f"symbols.{symbol}.risk.{key} must be greater than or equal to 0")
        parsed[key] = float(value)
    return parsed


def _validate_symbol_config(symbol: str, raw_config: Any) -> OnchainSymbolConfig:
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("symbols keys must be non-empty strings")
    normalized_symbol = _validate_upper(symbol, "symbol", symbol)
    raw = _require_mapping(raw_config, f"symbols.{symbol}")

    source_symbol = _validate_upper(
        _require_string(raw, "source_symbol", normalized_symbol),
        "source_symbol",
        normalized_symbol,
    )

    return OnchainSymbolConfig(
        symbol=normalized_symbol,
        enabled=_require_bool(raw, "enabled", normalized_symbol),
        signal_source=_require_string(raw, "signal_source", normalized_symbol),
        source_symbol=source_symbol,
        execution_session_filter=_optional_execution_session_filter(raw, normalized_symbol),
        chain_name=_require_string(raw, "chain_name", normalized_symbol),
        chain_id=_require_string(raw, "chain_id", normalized_symbol),
        token_symbol=_require_string(raw, "token_symbol", normalized_symbol),
        token_name=_require_string(raw, "token_name", normalized_symbol),
        token_address=_validate_address(
            _require_string(raw, "token_address", normalized_symbol),
            "token_address",
            normalized_symbol,
        ),
        token_decimals=_require_positive_int(raw, "token_decimals", normalized_symbol),
        quote_token_symbol=_require_string(raw, "quote_token_symbol", normalized_symbol),
        quote_token_address=_validate_address(
            _require_string(raw, "quote_token_address", normalized_symbol),
            "quote_token_address",
            normalized_symbol,
        ),
        quote_token_decimals=_require_positive_int(raw, "quote_token_decimals", normalized_symbol),
        max_trade_usdt=_require_positive_number(raw, "max_trade_usdt", normalized_symbol),
        max_slippage_pct=_require_non_negative_number(raw, "max_slippage_pct", normalized_symbol),
        max_gas_usdt=_require_non_negative_number(raw, "max_gas_usdt", normalized_symbol),
        risk=_optional_risk_overrides(raw, normalized_symbol),
    )


def load_onchain_symbols_config(
    symbols_path: Path | None = None,
) -> dict[str, OnchainSymbolConfig]:
    path = symbols_path or DEFAULT_ONCHAIN_SYMBOLS_PATH
    raw_config = load_yaml_mapping(path)
    raw_symbols = raw_config.get("symbols")
    if raw_symbols is None:
        raise ValueError(f"Missing top-level symbols in {path}")
    if not isinstance(raw_symbols, dict):
        raise ValueError(f"symbols must be a mapping in {path}")

    loaded: dict[str, OnchainSymbolConfig] = {}
    for symbol, raw_symbol_config in raw_symbols.items():
        symbol_config = _validate_symbol_config(str(symbol), raw_symbol_config)
        loaded[symbol_config.symbol] = symbol_config
    return loaded


def _settings_bool(raw: dict[str, Any], key: str, section: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{section}.{key} must be boolean")
    return value


def _settings_positive_int(raw: dict[str, Any], key: str, section: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{section}.{key} must be an integer greater than 0")
    if value <= 0:
        raise ValueError(f"{section}.{key} must be greater than 0")
    return value


def _settings_positive_number(raw: dict[str, Any], key: str, section: str) -> float:
    value = raw.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{section}.{key} must be a number greater than 0")
    if value <= 0:
        raise ValueError(f"{section}.{key} must be greater than 0")
    return float(value)


def _settings_non_negative_number(raw: dict[str, Any], key: str, section: str, default: float) -> float:
    value = raw.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{section}.{key} must be a number greater than or equal to 0")
    if value < 0:
        raise ValueError(f"{section}.{key} must be greater than or equal to 0")
    return float(value)


def load_onchain_settings_config(
    settings_path: Path | None = None,
) -> OnchainSettings:
    path = settings_path or DEFAULT_ONCHAIN_SETTINGS_PATH
    raw_config = load_yaml_mapping(path)
    app = _require_mapping(raw_config.get("app"), "app")
    quote = _require_mapping(raw_config.get("quote"), "quote")
    live = raw_config.get("live")
    live = live if isinstance(live, dict) else {}
    safety = _require_mapping(raw_config.get("safety"), "safety")
    risk = raw_config.get("risk")
    risk = risk if isinstance(risk, dict) else {}

    mode = app.get("mode")
    if not isinstance(mode, str) or not mode:
        raise ValueError("app.mode must be a non-empty string")

    return OnchainSettings(
        app_mode=mode,
        polling_interval_seconds=_settings_positive_int(app, "polling_interval_seconds", "app"),
        quote_auto_refresh_enabled=_settings_bool(quote, "auto_refresh_enabled", "quote"),
        quote_stale_seconds=_settings_positive_int(quote, "quote_stale_seconds", "quote"),
        quote_default_amount_usdt=_settings_positive_number(quote, "default_amount_usdt", "quote"),
        live_auto_live_enabled=bool(live.get("auto_live_enabled", False)),
        live_default_order_amount_usdt=_settings_positive_number(
            {"default_order_amount_usdt": live.get("default_order_amount_usdt", 20)},
            "default_order_amount_usdt",
            "live",
        ),
        live_require_manual_confirm_env=bool(live.get("require_manual_confirm_env", True)),
        live_wallet_signing_enabled=bool(live.get("wallet_signing_enabled", False)),
        live_broadcast_enabled=bool(live.get("broadcast_enabled", False)),
        live_require_wallet_env=bool(live.get("require_wallet_env", True)),
        live_max_live_order_usdt=_settings_positive_number(
            {"max_live_order_usdt": live.get("max_live_order_usdt", risk.get("max_live_order_usdt", 20))},
            "max_live_order_usdt",
            "live",
        ),
        live_max_live_trades_per_day=_settings_positive_int(
            {"max_live_trades_per_day": live.get("max_live_trades_per_day", risk.get("max_live_trades_per_day", 3))},
            "max_live_trades_per_day",
            "live",
        ),
        safety_allow_live_trading=_settings_bool(safety, "allow_live_trading", "safety"),
        safety_live_execute_enabled=_settings_bool(safety, "live_execute_enabled", "safety"),
        risk_max_price_impact_pct=_settings_non_negative_number(risk, "max_price_impact_pct", "risk", 1.0),
        risk_max_slippage_pct=_settings_non_negative_number(risk, "max_slippage_pct", "risk", 1.0),
        risk_max_gas_raw=_settings_non_negative_number(risk, "max_gas_raw", "risk", 500000),
        risk_quote_stale_seconds=_settings_positive_int(
            {"quote_stale_seconds": risk.get("quote_stale_seconds", 600)},
            "quote_stale_seconds",
            "risk",
        ),
        risk_max_token_tax_rate_pct=_settings_non_negative_number(risk, "max_token_tax_rate_pct", "risk", 0.0),
        risk_max_trade_usdt=_settings_positive_number({"max_trade_usdt": risk.get("max_trade_usdt", 50)}, "max_trade_usdt", "risk"),
        risk_max_live_order_usdt=_settings_positive_number(
            {"max_live_order_usdt": risk.get("max_live_order_usdt", 20)},
            "max_live_order_usdt",
            "risk",
        ),
        risk_max_live_trades_per_day=_settings_positive_int(
            {"max_live_trades_per_day": risk.get("max_live_trades_per_day", 3)},
            "max_live_trades_per_day",
            "risk",
        ),
        risk_max_open_positions=_settings_positive_int(
            {"max_open_positions": risk.get("max_open_positions", 3)},
            "max_open_positions",
            "risk",
        ),
        risk_max_opens_per_day=_settings_positive_int(
            {"max_opens_per_day": risk.get("max_opens_per_day", 5)},
            "max_opens_per_day",
            "risk",
        ),
        risk_max_closes_per_day=_settings_positive_int(
            {"max_closes_per_day": risk.get("max_closes_per_day", 5)},
            "max_closes_per_day",
            "risk",
        ),
        risk_min_trade_interval_seconds=int(_settings_non_negative_number(
            {"min_trade_interval_seconds": risk.get("min_trade_interval_seconds", 300)},
            "min_trade_interval_seconds",
            "risk",
            300,
        )),
    )


def dump_onchain_settings_yaml(settings: OnchainSettings) -> str:
    data = {
        "app": {
            "mode": settings.app_mode,
            "polling_interval_seconds": settings.polling_interval_seconds,
        },
        "quote": {
            "auto_refresh_enabled": settings.quote_auto_refresh_enabled,
            "quote_stale_seconds": settings.quote_stale_seconds,
            "default_amount_usdt": settings.quote_default_amount_usdt,
        },
        "live": {
            "auto_live_enabled": settings.live_auto_live_enabled,
            "default_order_amount_usdt": settings.live_default_order_amount_usdt,
            "require_manual_confirm_env": settings.live_require_manual_confirm_env,
            "wallet_signing_enabled": settings.live_wallet_signing_enabled,
            "broadcast_enabled": settings.live_broadcast_enabled,
            "require_wallet_env": settings.live_require_wallet_env,
            "max_live_order_usdt": settings.live_max_live_order_usdt,
            "max_live_trades_per_day": settings.live_max_live_trades_per_day,
        },
        "safety": {
            "allow_live_trading": settings.safety_allow_live_trading,
            "live_execute_enabled": settings.safety_live_execute_enabled,
        },
        "risk": {
            "max_price_impact_pct": settings.risk_max_price_impact_pct,
            "max_slippage_pct": settings.risk_max_slippage_pct,
            "max_gas_raw": settings.risk_max_gas_raw,
            "quote_stale_seconds": settings.risk_quote_stale_seconds,
            "max_token_tax_rate_pct": settings.risk_max_token_tax_rate_pct,
            "max_trade_usdt": settings.risk_max_trade_usdt,
            "max_live_order_usdt": settings.risk_max_live_order_usdt,
            "max_live_trades_per_day": settings.risk_max_live_trades_per_day,
            "max_open_positions": settings.risk_max_open_positions,
            "max_opens_per_day": settings.risk_max_opens_per_day,
            "max_closes_per_day": settings.risk_max_closes_per_day,
            "min_trade_interval_seconds": settings.risk_min_trade_interval_seconds,
        },
    }

    def write_value(lines: list[str], key: str, value: Any, indent: int) -> None:
        prefix = " " * indent
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            for child_key, child_value in value.items():
                write_value(lines, str(child_key), child_value, indent + 2)
        else:
            lines.append(f"{prefix}{key}: {_format_yaml_scalar(value)}")

    lines: list[str] = []
    for key, value in data.items():
        write_value(lines, key, value, 0)
    return "\n".join(lines) + "\n"


def save_onchain_settings_config(
    settings: OnchainSettings,
    settings_path: Path | None = None,
) -> dict[str, Any]:
    path = settings_path or DEFAULT_ONCHAIN_SETTINGS_PATH
    hard_max = min(settings.live_max_live_order_usdt, settings.risk_max_live_order_usdt)
    if settings.live_default_order_amount_usdt > hard_max:
        raise ValueError("live.default_order_amount_usdt cannot exceed max_live_order_usdt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_onchain_settings_yaml(settings), encoding="utf-8")
    return {
        "ok": True,
        "path": str(path),
    }


def _format_yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return f'"{value}"'


def dump_onchain_symbols_yaml(symbols: dict[str, OnchainSymbolConfig]) -> str:
    if not symbols:
        return "symbols: {}\n"

    lines = ["symbols:"]
    for symbol in sorted(symbols):
        symbol_config = symbols[symbol]
        lines.extend(
            [
                f"  {symbol}:",
                f"    enabled: {_format_yaml_scalar(symbol_config.enabled)}",
                f"    signal_source: {_format_yaml_scalar(symbol_config.signal_source)}",
                f"    source_symbol: {_format_yaml_scalar(symbol_config.source_symbol)}",
                f"    execution_session_filter: {_format_yaml_scalar(symbol_config.execution_session_filter)}",
                f"    chain_name: {_format_yaml_scalar(symbol_config.chain_name)}",
                f"    chain_id: {_format_yaml_scalar(symbol_config.chain_id)}",
                f"    token_symbol: {_format_yaml_scalar(symbol_config.token_symbol)}",
                f"    token_name: {_format_yaml_scalar(symbol_config.token_name)}",
                f"    token_address: {_format_yaml_scalar(symbol_config.token_address)}",
                f"    token_decimals: {_format_yaml_scalar(symbol_config.token_decimals)}",
                f"    quote_token_symbol: {_format_yaml_scalar(symbol_config.quote_token_symbol)}",
                f"    quote_token_address: {_format_yaml_scalar(symbol_config.quote_token_address)}",
                f"    quote_token_decimals: {_format_yaml_scalar(symbol_config.quote_token_decimals)}",
                f"    max_trade_usdt: {_format_yaml_scalar(symbol_config.max_trade_usdt)}",
                f"    max_slippage_pct: {_format_yaml_scalar(symbol_config.max_slippage_pct)}",
                f"    max_gas_usdt: {_format_yaml_scalar(symbol_config.max_gas_usdt)}",
            ]
        )
        if symbol_config.risk:
            lines.append("    risk:")
            for key in RISK_KEYS:
                if key in symbol_config.risk:
                    lines.append(f"      {key}: {_format_yaml_scalar(symbol_config.risk[key])}")
    return "\n".join(lines) + "\n"


def save_onchain_symbols_config(
    symbols: dict[str, OnchainSymbolConfig | dict[str, Any]],
    symbols_path: Path | None = None,
) -> dict[str, Any]:
    path = symbols_path or DEFAULT_ONCHAIN_SYMBOLS_PATH
    validated: dict[str, OnchainSymbolConfig] = {}
    for symbol, symbol_config in symbols.items():
        if isinstance(symbol_config, OnchainSymbolConfig):
            raw_config = symbol_config.to_dict()
        else:
            raw_config = symbol_config
        validated_config = _validate_symbol_config(symbol, raw_config)
        validated[validated_config.symbol] = validated_config

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_onchain_symbols_yaml(validated), encoding="utf-8")
    return {
        "ok": True,
        "symbols_count": len(validated),
        "path": str(path),
    }


def onchain_symbols_payload(
    symbols_path: Path | None = None,
) -> dict[str, Any]:
    symbols = load_onchain_symbols_config(symbols_path)
    enabled_symbols = [
        symbol
        for symbol, symbol_config in symbols.items()
        if symbol_config.enabled
    ]
    return {
        "symbols_count": len(symbols),
        "enabled_symbols": enabled_symbols,
        "symbols": {
            symbol: symbol_config.to_dict()
            for symbol, symbol_config in symbols.items()
        },
    }
