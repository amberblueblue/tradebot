from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_FUTURES_SETTINGS_PATH = CONFIG_DIR / "futures_settings.yaml"
DEFAULT_FUTURES_SYMBOLS_PATH = CONFIG_DIR / "futures_symbols.yaml"


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
    max_position_ratio: float
    min_liquidation_distance_pct: float
    max_funding_rate_abs: float
    max_consecutive_losing_trades: int


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


def _require_string(config: dict[str, Any], key: str, path: Path) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string in {path}")
    return value


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
    max_funding_rate_abs = risk_config.get("max_funding_rate_abs", 0)
    if not isinstance(max_funding_rate_abs, (int, float)) or isinstance(max_funding_rate_abs, bool):
        raise ValueError(f"risk.max_funding_rate_abs must be a number in {settings_path}")
    if max_funding_rate_abs < 0:
        raise ValueError(f"risk.max_funding_rate_abs must be greater than or equal to 0 in {settings_path}")

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

    return FuturesRiskConfig(
        max_leverage=_require_positive_number(risk_config, "max_leverage", settings_path),
        max_margin_per_trade_usdt=_require_positive_number(
            risk_config,
            "max_margin_per_trade_usdt",
            settings_path,
        ),
        max_position_ratio=float(max_position_ratio),
        min_liquidation_distance_pct=_require_positive_number(
            risk_config,
            "min_liquidation_distance_pct",
            settings_path,
        ),
        max_funding_rate_abs=float(max_funding_rate_abs),
        max_consecutive_losing_trades=max_consecutive_losing_trades,
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
) -> dict[str, FuturesSymbolConfig]:
    raw_symbols = symbols_config.get("symbols", {})
    if raw_symbols is None:
        raw_symbols = {}
    if not isinstance(raw_symbols, dict):
        raise ValueError(f"symbols must be a mapping in {symbols_path}")

    loaded_symbols: dict[str, FuturesSymbolConfig] = {}
    for symbol, raw_symbol_config in raw_symbols.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol names must be non-empty strings in {symbols_path}")
        if not isinstance(raw_symbol_config, dict):
            raise ValueError(f"symbols.{symbol} must be a mapping in {symbols_path}")

        enabled = raw_symbol_config.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError(f"symbols.{symbol}.enabled must be a boolean in {symbols_path}")

        loaded_symbols[symbol] = FuturesSymbolConfig(
            symbol=symbol,
            enabled=enabled,
            strategy=str(raw_symbol_config.get("strategy", "")),
            leverage=float(raw_symbol_config.get("leverage", 0)),
            margin_amount=float(raw_symbol_config.get("margin_amount", 0)),
            trend_timeframe=str(raw_symbol_config.get("trend_timeframe", "")),
            signal_timeframe=str(raw_symbol_config.get("signal_timeframe", "")),
        )

    return loaded_symbols


def load_futures_config(
    settings_path: Path = DEFAULT_FUTURES_SETTINGS_PATH,
    symbols_path: Path = DEFAULT_FUTURES_SYMBOLS_PATH,
) -> FuturesRuntimeConfig:
    settings = load_yaml_mapping(settings_path)
    symbols = load_yaml_mapping(symbols_path)

    return FuturesRuntimeConfig(
        settings_path=settings_path,
        symbols_path=symbols_path,
        app=_load_app_config(settings, settings_path),
        futures=_load_futures_endpoint_config(settings, settings_path),
        risk=_load_risk_config(settings, settings_path),
        safety=_load_safety_config(settings, settings_path),
        symbols=_load_symbol_configs(symbols, symbols_path),
    )
