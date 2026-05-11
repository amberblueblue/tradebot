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
    quote_default_amount_usdc: float
    live_auto_live_enabled: bool
    live_validation_mode: bool
    live_validation_max_order_usdc: float
    live_default_order_amount_usdc: float
    live_require_manual_confirm_env: bool
    live_wallet_signing_enabled: bool
    live_broadcast_enabled: bool
    live_approve_enabled: bool
    live_approve_mode: str
    live_require_wallet_env: bool
    live_max_live_order_usdc: float
    live_max_live_trades_per_day: int
    safety_allow_live_trading: bool
    safety_live_execute_enabled: bool
    risk_max_price_impact_pct: float
    risk_max_slippage_pct: float
    risk_max_gas_raw: float
    risk_quote_stale_seconds: int
    risk_max_token_tax_rate_pct: float
    risk_max_trade_usdc: float
    risk_max_live_order_usdc: float
    risk_max_live_trades_per_day: int
    risk_max_open_positions: int
    risk_max_opens_per_day: int
    risk_max_closes_per_day: int
    risk_min_trade_interval_seconds: int
    rpc_ethereum: str
    rpc_base: str
    rpc_arbitrum: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            {
                "quote_default_amount_usdt": self.quote_default_amount_usdc,
                "live_validation_max_order_usdt": self.live_validation_max_order_usdc,
                "live_default_order_amount_usdt": self.live_default_order_amount_usdc,
                "live_max_live_order_usdt": self.live_max_live_order_usdc,
                "risk_max_trade_usdt": self.risk_max_trade_usdc,
                "risk_max_live_order_usdt": self.risk_max_live_order_usdc,
            }
        )
        return data

    @property
    def quote_default_amount_usdt(self) -> float:
        return self.quote_default_amount_usdc

    @property
    def live_validation_max_order_usdt(self) -> float:
        return self.live_validation_max_order_usdc

    @property
    def live_default_order_amount_usdt(self) -> float:
        return self.live_default_order_amount_usdc

    @property
    def live_max_live_order_usdt(self) -> float:
        return self.live_max_live_order_usdc

    @property
    def risk_max_trade_usdt(self) -> float:
        return self.risk_max_trade_usdc

    @property
    def risk_max_live_order_usdt(self) -> float:
        return self.risk_max_live_order_usdc


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
    max_trade_usdc: float
    max_slippage_pct: float
    max_gas_usdc: float
    risk: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            {
                "max_trade_usdt": self.max_trade_usdc,
                "max_gas_usdt": self.max_gas_usdc,
            }
        )
        return data

    @property
    def max_trade_usdt(self) -> float:
        return self.max_trade_usdc

    @property
    def max_gas_usdt(self) -> float:
        return self.max_gas_usdc


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
    "max_trade_usdc",
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
        if key in {"max_trade_usdc", "max_trade_usdt"} and value <= 0:
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
        max_trade_usdc=_require_positive_number(
            {"max_trade_usdc": raw.get("max_trade_usdc", raw.get("max_trade_usdt"))},
            "max_trade_usdc",
            normalized_symbol,
        ),
        max_slippage_pct=_require_non_negative_number(raw, "max_slippage_pct", normalized_symbol),
        max_gas_usdc=_require_non_negative_number(
            {"max_gas_usdc": raw.get("max_gas_usdc", raw.get("max_gas_usdt"))},
            "max_gas_usdc",
            normalized_symbol,
        ),
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
    rpc = raw_config.get("rpc")
    rpc = rpc if isinstance(rpc, dict) else {}

    mode = app.get("mode")
    if not isinstance(mode, str) or not mode:
        raise ValueError("app.mode must be a non-empty string")

    return OnchainSettings(
        app_mode=mode,
        polling_interval_seconds=_settings_positive_int(app, "polling_interval_seconds", "app"),
        quote_auto_refresh_enabled=_settings_bool(quote, "auto_refresh_enabled", "quote"),
        quote_stale_seconds=_settings_positive_int(quote, "quote_stale_seconds", "quote"),
        quote_default_amount_usdc=_settings_positive_number(
            {"default_amount_usdc": quote.get("default_amount_usdc", quote.get("default_amount_usdt"))},
            "default_amount_usdc",
            "quote",
        ),
        live_auto_live_enabled=bool(live.get("auto_live_enabled", False)),
        live_validation_mode=bool(live.get("validation_mode", True)),
        live_validation_max_order_usdc=_settings_positive_number(
            {"validation_max_order_usdc": live.get("validation_max_order_usdc", live.get("validation_max_order_usdt", 5))},
            "validation_max_order_usdc",
            "live",
        ),
        live_default_order_amount_usdc=_settings_positive_number(
            {"default_order_amount_usdc": live.get("default_order_amount_usdc", live.get("default_order_amount_usdt", 20))},
            "default_order_amount_usdc",
            "live",
        ),
        live_require_manual_confirm_env=bool(live.get("require_manual_confirm_env", True)),
        live_wallet_signing_enabled=bool(live.get("wallet_signing_enabled", False)),
        live_broadcast_enabled=bool(live.get("broadcast_enabled", False)),
        live_approve_enabled=bool(live.get("approve_enabled", False)),
        live_approve_mode=str(live.get("approve_mode", "exact_amount") or "exact_amount"),
        live_require_wallet_env=bool(live.get("require_wallet_env", True)),
        live_max_live_order_usdc=_settings_positive_number(
            {
                "max_live_order_usdc": live.get(
                    "max_live_order_usdc",
                    live.get("max_live_order_usdt", risk.get("max_live_order_usdc", risk.get("max_live_order_usdt", 20))),
                )
            },
            "max_live_order_usdc",
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
        risk_max_trade_usdc=_settings_positive_number(
            {"max_trade_usdc": risk.get("max_trade_usdc", risk.get("max_trade_usdt", 50))},
            "max_trade_usdc",
            "risk",
        ),
        risk_max_live_order_usdc=_settings_positive_number(
            {"max_live_order_usdc": risk.get("max_live_order_usdc", risk.get("max_live_order_usdt", 20))},
            "max_live_order_usdc",
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
        rpc_ethereum=str(rpc.get("ethereum", "") or ""),
        rpc_base=str(rpc.get("base", "") or ""),
        rpc_arbitrum=str(rpc.get("arbitrum", "") or ""),
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
            "default_amount_usdc": settings.quote_default_amount_usdc,
        },
        "live": {
            "auto_live_enabled": settings.live_auto_live_enabled,
            "validation_mode": settings.live_validation_mode,
            "validation_max_order_usdc": settings.live_validation_max_order_usdc,
            "default_order_amount_usdc": settings.live_default_order_amount_usdc,
            "require_manual_confirm_env": settings.live_require_manual_confirm_env,
            "wallet_signing_enabled": settings.live_wallet_signing_enabled,
            "broadcast_enabled": settings.live_broadcast_enabled,
            "approve_enabled": settings.live_approve_enabled,
            "approve_mode": settings.live_approve_mode,
            "require_wallet_env": settings.live_require_wallet_env,
            "max_live_order_usdc": settings.live_max_live_order_usdc,
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
            "max_trade_usdc": settings.risk_max_trade_usdc,
            "max_live_order_usdc": settings.risk_max_live_order_usdc,
            "max_live_trades_per_day": settings.risk_max_live_trades_per_day,
            "max_open_positions": settings.risk_max_open_positions,
            "max_opens_per_day": settings.risk_max_opens_per_day,
            "max_closes_per_day": settings.risk_max_closes_per_day,
            "min_trade_interval_seconds": settings.risk_min_trade_interval_seconds,
        },
        "rpc": {
            "ethereum": settings.rpc_ethereum,
            "base": settings.rpc_base,
            "arbitrum": settings.rpc_arbitrum,
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
    hard_max = min(settings.live_max_live_order_usdc, settings.risk_max_live_order_usdc)
    if settings.live_default_order_amount_usdc > hard_max:
        raise ValueError("live.default_order_amount_usdc cannot exceed max_live_order_usdc")
    if settings.live_approve_mode != "exact_amount":
        raise ValueError("live.approve_mode must be exact_amount")
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
                f"    max_trade_usdc: {_format_yaml_scalar(symbol_config.max_trade_usdc)}",
                f"    max_slippage_pct: {_format_yaml_scalar(symbol_config.max_slippage_pct)}",
                f"    max_gas_usdc: {_format_yaml_scalar(symbol_config.max_gas_usdc)}",
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
