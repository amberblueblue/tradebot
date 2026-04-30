from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_FUTURES_SETTINGS_PATH = CONFIG_DIR / "futures_settings.yaml"
DEFAULT_FUTURES_SYMBOLS_PATH = CONFIG_DIR / "futures_symbols.yaml"
ALLOWED_FUTURES_STRATEGIES = {"trend_long", "trend_long_test"}
ALLOWED_FUTURES_TIMEFRAMES = {"5m", "15m", "1h", "4h", "1d"}
DEFAULT_FUTURES_RISK_SETTINGS: dict[str, int | float] = {
    "max_single_order_usdt": 20,
    "max_consecutive_losing_trades": 4,
    "stop_loss_pct": 20.0,
    "partial1_sell_pct": 30.0,
    "partial2_sell_pct": 50.0,
    "big_candle_multiplier": 1.5,
    "big_candle_body_lookback": 20,
    "profit_giveback_ratio": 0.5,
    "profit_protection_trigger_pct": 15.0,
}
DEFAULT_FUTURES_STRATEGY_SETTINGS: dict[str, dict[str, int | float]] = {
    "trend_long": {
        "ema_fast": 44,
        "ema_slow": 144,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "rsi_period": 14,
        "min_rsi": 45,
        "max_rsi": 75,
    },
    "trend_long_test": {
        "ema_fast": 44,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "rsi_period": 14,
    },
}


@dataclass(frozen=True)
class FuturesAppConfig:
    mode: str
    polling_interval_seconds: int


@dataclass(frozen=True)
class FuturesEndpointConfig:
    base_url: str
    request_timeout_seconds: int
    rules_cache_ttl_seconds: int


@dataclass(frozen=True)
class FuturesRiskConfig:
    max_leverage: float
    max_margin_per_trade_usdt: float
    max_single_order_usdt: float
    max_position_ratio: float
    min_liquidation_distance_pct: float
    max_funding_rate_abs: float
    paper_test_max_funding_rate_abs: float
    max_consecutive_losing_trades: int
    stop_loss_pct: float
    partial1_sell_pct: float
    partial2_sell_pct: float
    big_candle_multiplier: float
    big_candle_body_lookback: int
    profit_giveback_ratio: float
    profit_protection_trigger_pct: float


@dataclass(frozen=True)
class FuturesSafetyConfig:
    allow_live_trading: bool
    live_execute_enabled: bool


@dataclass(frozen=True)
class FuturesSymbolConfig:
    symbol: str
    enabled: bool
    strategy: str
    leverage: float
    margin_amount: float
    trend_timeframe: str
    signal_timeframe: str


@dataclass(frozen=True)
class FuturesRuntimeConfig:
    settings_path: Path
    symbols_path: Path
    app: FuturesAppConfig
    futures: FuturesEndpointConfig
    risk: FuturesRiskConfig
    safety: FuturesSafetyConfig
    symbols: dict[str, FuturesSymbolConfig]

    @property
    def enabled_symbols(self) -> tuple[str, ...]:
        return tuple(
            symbol
            for symbol, symbol_config in self.symbols.items()
            if symbol_config.enabled
        )


def _strip_comments(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def _normalize_lines(text: str) -> list[tuple[int, str]]:
    normalized: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        cleaned = _strip_comments(raw_line).rstrip()
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        normalized.append((indent, cleaned.strip()))
    return normalized


def _parse_scalar(value: str) -> Any:
    if value in {"null", "~"}:
        return None
    if value == "{}":
        return {}
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index

    current_indent, current_text = lines[index]
    if current_indent != indent:
        raise ValueError(f"Invalid indentation near '{current_text}'")

    mapping: dict[str, Any] = {}
    while index < len(lines):
        current_indent, current_text = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or current_text.startswith("- "):
            raise ValueError(f"Invalid mapping entry near '{current_text}'")

        key, separator, remainder = current_text.partition(":")
        if separator != ":":
            raise ValueError(f"Invalid mapping entry near '{current_text}'")

        key = key.strip()
        remainder = remainder.strip()
        index += 1
        if remainder:
            mapping[key] = _parse_scalar(remainder)
            continue

        if index >= len(lines) or lines[index][0] <= current_indent:
            mapping[key] = {}
            continue

        nested_indent = lines[index][0]
        nested_value, index = _parse_block(lines, index, nested_indent)
        mapping[key] = nested_value

    return mapping, index


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing futures config file: {path}")
    parsed, _ = _parse_block(_normalize_lines(path.read_text(encoding="utf-8")), 0, 0)
    if not isinstance(parsed, dict):
        raise ValueError(f"Top-level YAML content must be a mapping: {path}")
    return parsed


def load_futures_strategy_settings(
    strategy_name: str,
    settings_path: Path | None = None,
) -> dict[str, int | float]:
    defaults = DEFAULT_FUTURES_STRATEGY_SETTINGS.get(strategy_name, {})
    result: dict[str, int | float] = dict(defaults)
    path = settings_path or DEFAULT_FUTURES_SETTINGS_PATH
    try:
        settings = load_yaml_mapping(path)
        strategy_config = settings.get("strategy", {})
        if not isinstance(strategy_config, dict):
            return result
        configured = strategy_config.get(strategy_name, {})
        if not isinstance(configured, dict):
            return result
    except Exception:
        return result

    for key, default_value in defaults.items():
        value = configured.get(key, default_value)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value <= 0:
            continue
        result[key] = int(value) if isinstance(default_value, int) else float(value)
    return result


def _require_mapping(config: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = config.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping in {path}")
    return value


def _require_positive_number(config: dict[str, Any], key: str, path: Path) -> float:
    value = config.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} must be a number in {path}")
    if value <= 0:
        raise ValueError(f"{key} must be greater than 0 in {path}")
    return float(value)


def _positive_number_with_default(
    config: dict[str, Any],
    key: str,
    path: Path,
    default: int | float,
) -> float:
    value = config.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"risk.{key} must be a number in {path}")
    if value <= 0:
        raise ValueError(f"risk.{key} must be greater than 0 in {path}")
    return float(value)


def _positive_int_with_default(
    config: dict[str, Any],
    key: str,
    path: Path,
    default: int | float,
) -> int:
    value = config.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"risk.{key} must be an integer in {path}")
    if value <= 0:
        raise ValueError(f"risk.{key} must be greater than 0 in {path}")
    return value


def _bounded_positive_number_with_default(
    config: dict[str, Any],
    key: str,
    path: Path,
    default: int | float,
    upper_bound: float,
) -> float:
    value = _positive_number_with_default(config, key, path, default)
    if value > upper_bound:
        raise ValueError(f"risk.{key} must be less than or equal to {upper_bound} in {path}")
    return value


def _require_string(config: dict[str, Any], key: str, path: Path) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string in {path}")
    return value


def _require_boolean(config: dict[str, Any], key: str, path: Path) -> bool:
    value = config.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean in {path}")
    return value


def _format_yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return f'"{value}"'


def _load_app_config(settings: dict[str, Any], settings_path: Path) -> FuturesAppConfig:
    app_config = _require_mapping(settings, "app", settings_path)
    mode = app_config.get("mode")
    if mode not in {"paper", "live"}:
        raise ValueError("app.mode must be paper or live")

    polling_interval = app_config.get("polling_interval_seconds", 30)
    if not isinstance(polling_interval, int) or isinstance(polling_interval, bool):
        raise ValueError(f"app.polling_interval_seconds must be an integer in {settings_path}")
    if polling_interval <= 0:
        raise ValueError(f"app.polling_interval_seconds must be greater than 0 in {settings_path}")

    return FuturesAppConfig(
        mode=mode,
        polling_interval_seconds=polling_interval,
    )


def _load_futures_endpoint_config(
    settings: dict[str, Any],
    settings_path: Path,
) -> FuturesEndpointConfig:
    futures_config = _require_mapping(settings, "futures", settings_path)
    request_timeout = futures_config.get("request_timeout_seconds", 10)
    rules_cache_ttl = futures_config.get("rules_cache_ttl_seconds", 3600)

    if not isinstance(request_timeout, int) or isinstance(request_timeout, bool):
        raise ValueError(f"futures.request_timeout_seconds must be an integer in {settings_path}")
    if request_timeout <= 0:
        raise ValueError(f"futures.request_timeout_seconds must be greater than 0 in {settings_path}")
    if not isinstance(rules_cache_ttl, int) or isinstance(rules_cache_ttl, bool):
        raise ValueError(f"futures.rules_cache_ttl_seconds must be an integer in {settings_path}")
    if rules_cache_ttl <= 0:
        raise ValueError(f"futures.rules_cache_ttl_seconds must be greater than 0 in {settings_path}")

    return FuturesEndpointConfig(
        base_url=_require_string(futures_config, "base_url", settings_path),
        request_timeout_seconds=request_timeout,
        rules_cache_ttl_seconds=rules_cache_ttl,
    )


def _load_risk_config(settings: dict[str, Any], settings_path: Path) -> FuturesRiskConfig:
    risk_config = _require_mapping(settings, "risk", settings_path)
    max_single_order_usdt = _positive_number_with_default(
        risk_config,
        "max_single_order_usdt",
        settings_path,
        DEFAULT_FUTURES_RISK_SETTINGS["max_single_order_usdt"],
    )
    max_funding_rate_abs = risk_config.get("max_funding_rate_abs", 0)
    if not isinstance(max_funding_rate_abs, (int, float)) or isinstance(max_funding_rate_abs, bool):
        raise ValueError(f"risk.max_funding_rate_abs must be a number in {settings_path}")
    if max_funding_rate_abs < 0:
        raise ValueError(f"risk.max_funding_rate_abs must be greater than or equal to 0 in {settings_path}")

    paper_test_max_funding_rate_abs = risk_config.get(
        "paper_test_max_funding_rate_abs",
        max_funding_rate_abs,
    )
    if (
        not isinstance(paper_test_max_funding_rate_abs, (int, float))
        or isinstance(paper_test_max_funding_rate_abs, bool)
    ):
        raise ValueError(f"risk.paper_test_max_funding_rate_abs must be a number in {settings_path}")
    if paper_test_max_funding_rate_abs < 0:
        raise ValueError(
            f"risk.paper_test_max_funding_rate_abs must be greater than or equal to 0 in {settings_path}"
        )

    max_position_ratio = risk_config.get("max_position_ratio")
    if not isinstance(max_position_ratio, (int, float)) or isinstance(max_position_ratio, bool):
        raise ValueError(f"risk.max_position_ratio must be a number in {settings_path}")
    if not 0 < max_position_ratio <= 1:
        raise ValueError(f"risk.max_position_ratio must be greater than 0 and less than or equal to 1 in {settings_path}")

    max_consecutive_losing_trades = risk_config.get("max_consecutive_losing_trades")
    if (
        not isinstance(max_consecutive_losing_trades, int)
        or isinstance(max_consecutive_losing_trades, bool)
    ):
        raise ValueError(f"risk.max_consecutive_losing_trades must be an integer in {settings_path}")
    if max_consecutive_losing_trades <= 0:
        raise ValueError(f"risk.max_consecutive_losing_trades must be greater than 0 in {settings_path}")

    stop_loss_pct = _positive_number_with_default(
        risk_config,
        "stop_loss_pct",
        settings_path,
        DEFAULT_FUTURES_RISK_SETTINGS["stop_loss_pct"],
    )
    partial1_sell_pct = _bounded_positive_number_with_default(
        risk_config,
        "partial1_sell_pct",
        settings_path,
        DEFAULT_FUTURES_RISK_SETTINGS["partial1_sell_pct"],
        100,
    )
    partial2_sell_pct = _bounded_positive_number_with_default(
        risk_config,
        "partial2_sell_pct",
        settings_path,
        DEFAULT_FUTURES_RISK_SETTINGS["partial2_sell_pct"],
        100,
    )
    if partial1_sell_pct + partial2_sell_pct > 100:
        raise ValueError(f"risk.partial1_sell_pct + risk.partial2_sell_pct must be less than or equal to 100 in {settings_path}")
    big_candle_multiplier = _positive_number_with_default(
        risk_config,
        "big_candle_multiplier",
        settings_path,
        DEFAULT_FUTURES_RISK_SETTINGS["big_candle_multiplier"],
    )
    big_candle_body_lookback = _positive_int_with_default(
        risk_config,
        "big_candle_body_lookback",
        settings_path,
        DEFAULT_FUTURES_RISK_SETTINGS["big_candle_body_lookback"],
    )
    profit_giveback_ratio = _bounded_positive_number_with_default(
        risk_config,
        "profit_giveback_ratio",
        settings_path,
        DEFAULT_FUTURES_RISK_SETTINGS["profit_giveback_ratio"],
        1,
    )
    profit_protection_trigger_pct = _positive_number_with_default(
        risk_config,
        "profit_protection_trigger_pct",
        settings_path,
        DEFAULT_FUTURES_RISK_SETTINGS["profit_protection_trigger_pct"],
    )

    return FuturesRiskConfig(
        max_leverage=_require_positive_number(risk_config, "max_leverage", settings_path),
        max_margin_per_trade_usdt=_require_positive_number(
            risk_config,
            "max_margin_per_trade_usdt",
            settings_path,
        ),
        max_single_order_usdt=max_single_order_usdt,
        max_position_ratio=float(max_position_ratio),
        min_liquidation_distance_pct=_require_positive_number(
            risk_config,
            "min_liquidation_distance_pct",
            settings_path,
        ),
        max_funding_rate_abs=float(max_funding_rate_abs),
        paper_test_max_funding_rate_abs=float(paper_test_max_funding_rate_abs),
        max_consecutive_losing_trades=max_consecutive_losing_trades,
        stop_loss_pct=stop_loss_pct,
        partial1_sell_pct=partial1_sell_pct,
        partial2_sell_pct=partial2_sell_pct,
        big_candle_multiplier=big_candle_multiplier,
        big_candle_body_lookback=big_candle_body_lookback,
        profit_giveback_ratio=profit_giveback_ratio,
        profit_protection_trigger_pct=profit_protection_trigger_pct,
    )


def _load_safety_config(settings: dict[str, Any], settings_path: Path) -> FuturesSafetyConfig:
    safety_config = _require_mapping(settings, "safety", settings_path)
    allow_live_trading = safety_config.get("allow_live_trading", False)
    live_execute_enabled = safety_config.get("live_execute_enabled", False)

    if not isinstance(allow_live_trading, bool):
        raise ValueError(f"safety.allow_live_trading must be a boolean in {settings_path}")
    if not isinstance(live_execute_enabled, bool):
        raise ValueError(f"safety.live_execute_enabled must be a boolean in {settings_path}")

    return FuturesSafetyConfig(
        allow_live_trading=allow_live_trading,
        live_execute_enabled=live_execute_enabled,
    )


def _load_symbol_configs(
    symbols_config: dict[str, Any],
    symbols_path: Path,
    risk_config: FuturesRiskConfig,
) -> dict[str, FuturesSymbolConfig]:
    raw_symbols = symbols_config.get("symbols", {})
    if raw_symbols is None:
        raw_symbols = {}
    if not isinstance(raw_symbols, dict):
        raise ValueError(f"symbols must be a mapping in {symbols_path}")

    loaded_symbols = _validate_symbol_configs(raw_symbols, symbols_path, risk_config)

    return loaded_symbols


def _symbol_config_to_mapping(symbol_config: FuturesSymbolConfig | dict[str, Any]) -> dict[str, Any]:
    if isinstance(symbol_config, FuturesSymbolConfig):
        return {
            "enabled": symbol_config.enabled,
            "strategy": symbol_config.strategy,
            "leverage": symbol_config.leverage,
            "margin_amount": symbol_config.margin_amount,
            "trend_timeframe": symbol_config.trend_timeframe,
            "signal_timeframe": symbol_config.signal_timeframe,
        }
    if isinstance(symbol_config, dict):
        return dict(symbol_config)
    raise ValueError("futures symbol config must be a mapping")


def _validate_symbol_configs(
    raw_symbols: dict[str, Any],
    symbols_path: Path,
    risk_config: FuturesRiskConfig,
) -> dict[str, FuturesSymbolConfig]:
    loaded_symbols: dict[str, FuturesSymbolConfig] = {}
    for symbol, raw_symbol_config in raw_symbols.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol names must be non-empty strings in {symbols_path}")
        if symbol != symbol.upper():
            raise ValueError(f"symbols.{symbol} must be uppercase in {symbols_path}")
        if not symbol.endswith("USDT"):
            raise ValueError(f"symbols.{symbol} must end with USDT in {symbols_path}")

        raw_mapping = _symbol_config_to_mapping(raw_symbol_config)
        field_prefix = f"symbols.{symbol}"
        enabled = _require_boolean(raw_mapping, "enabled", symbols_path)
        strategy = _require_string(raw_mapping, "strategy", symbols_path)
        if strategy not in ALLOWED_FUTURES_STRATEGIES:
            raise ValueError(
                f"{field_prefix}.strategy must be one of {sorted(ALLOWED_FUTURES_STRATEGIES)} "
                f"in {symbols_path}"
            )

        leverage = _require_positive_number(raw_mapping, "leverage", symbols_path)
        if leverage > risk_config.max_leverage:
            raise ValueError(
                f"{field_prefix}.leverage must be less than or equal to "
                f"risk.max_leverage ({risk_config.max_leverage}) in {symbols_path}"
            )

        margin_amount = _require_positive_number(raw_mapping, "margin_amount", symbols_path)
        if margin_amount > risk_config.max_margin_per_trade_usdt:
            raise ValueError(
                f"{field_prefix}.margin_amount must be less than or equal to "
                "risk.max_margin_per_trade_usdt "
                f"({risk_config.max_margin_per_trade_usdt}) in {symbols_path}"
            )

        trend_timeframe = _require_string(raw_mapping, "trend_timeframe", symbols_path)
        if trend_timeframe not in ALLOWED_FUTURES_TIMEFRAMES:
            raise ValueError(
                f"{field_prefix}.trend_timeframe must be one of "
                f"{sorted(ALLOWED_FUTURES_TIMEFRAMES)} in {symbols_path}"
            )

        signal_timeframe = _require_string(raw_mapping, "signal_timeframe", symbols_path)
        if signal_timeframe not in ALLOWED_FUTURES_TIMEFRAMES:
            raise ValueError(
                f"{field_prefix}.signal_timeframe must be one of "
                f"{sorted(ALLOWED_FUTURES_TIMEFRAMES)} in {symbols_path}"
            )

        loaded_symbols[symbol] = FuturesSymbolConfig(
            symbol=symbol,
            enabled=enabled,
            strategy=strategy,
            leverage=leverage,
            margin_amount=margin_amount,
            trend_timeframe=trend_timeframe,
            signal_timeframe=signal_timeframe,
        )
    return loaded_symbols


def load_futures_symbols_config(
    settings_path: Path = DEFAULT_FUTURES_SETTINGS_PATH,
    symbols_path: Path = DEFAULT_FUTURES_SYMBOLS_PATH,
) -> dict[str, FuturesSymbolConfig]:
    settings = load_yaml_mapping(settings_path)
    symbols = load_yaml_mapping(symbols_path)
    risk_config = _load_risk_config(settings, settings_path)
    return _load_symbol_configs(symbols, symbols_path, risk_config)


def dump_futures_symbols_yaml(symbols: dict[str, FuturesSymbolConfig]) -> str:
    if not symbols:
        return "symbols: {}\n"

    lines = ["symbols:"]
    for symbol in sorted(symbols):
        symbol_config = symbols[symbol]
        lines.extend(
            [
                f"  {symbol}:",
                f"    enabled: {_format_yaml_scalar(symbol_config.enabled)}",
                f"    strategy: {_format_yaml_scalar(symbol_config.strategy)}",
                f"    leverage: {_format_yaml_scalar(symbol_config.leverage)}",
                f"    margin_amount: {_format_yaml_scalar(symbol_config.margin_amount)}",
                f"    trend_timeframe: {_format_yaml_scalar(symbol_config.trend_timeframe)}",
                f"    signal_timeframe: {_format_yaml_scalar(symbol_config.signal_timeframe)}",
            ]
        )
    return "\n".join(lines) + "\n"


def save_futures_symbols_config(
    symbols: dict[str, FuturesSymbolConfig | dict[str, Any]],
    settings_path: Path = DEFAULT_FUTURES_SETTINGS_PATH,
    symbols_path: Path = DEFAULT_FUTURES_SYMBOLS_PATH,
) -> dict[str, object]:
    try:
        settings = load_yaml_mapping(settings_path)
        risk_config = _load_risk_config(settings, settings_path)
        validated_symbols = _validate_symbol_configs(symbols, symbols_path, risk_config)
        symbols_path.parent.mkdir(parents=True, exist_ok=True)
        symbols_path.write_text(
            dump_futures_symbols_yaml(validated_symbols),
            encoding="utf-8",
        )
        return {
            "ok": True,
            "path": str(symbols_path),
            "symbols_count": len(validated_symbols),
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": str(symbols_path),
            "error": str(exc),
        }


def load_futures_config(
    settings_path: Path = DEFAULT_FUTURES_SETTINGS_PATH,
    symbols_path: Path = DEFAULT_FUTURES_SYMBOLS_PATH,
) -> FuturesRuntimeConfig:
    settings = load_yaml_mapping(settings_path)
    symbols = load_yaml_mapping(symbols_path)
    risk_config = _load_risk_config(settings, settings_path)

    return FuturesRuntimeConfig(
        settings_path=settings_path,
        symbols_path=symbols_path,
        app=_load_app_config(settings, settings_path),
        futures=_load_futures_endpoint_config(settings, settings_path),
        risk=risk_config,
        safety=_load_safety_config(settings, settings_path),
        symbols=_load_symbol_configs(symbols, symbols_path, risk_config),
    )
