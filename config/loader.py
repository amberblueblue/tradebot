from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
DEFAULT_SYMBOLS_PATH = CONFIG_DIR / "symbols.yaml"
VALID_SYMBOL_TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")
VALID_SYMBOL_TIMEFRAME_SET = frozenset(VALID_SYMBOL_TIMEFRAMES)


@dataclass(frozen=True)
class SymbolTradingConfig:
    symbol: str
    enabled: bool
    trend_timeframe: str
    signal_timeframe: str
    order_amount: float
    max_loss_amount: float
    paused_by_loss: bool


@dataclass(frozen=True)
class BacktestRuntimeConfig:
    mode: str
    exchange_name: str
    symbol: str
    symbol_list: tuple[str, ...]
    entry_timeframe: str
    trend_timeframe: str
    polling_interval_seconds: int
    initial_capital: float
    report_file: str
    log_file: str
    logging_level: str
    data_file_1h: str
    data_file_4h: str


@dataclass(frozen=True)
class ExchangeConfig:
    name: str
    base_url: str
    recv_window: int
    request_timeout_seconds: int


@dataclass(frozen=True)
class BinanceConfig:
    rules_cache_ttl_seconds: int


@dataclass(frozen=True)
class ExecutionRuntimeConfig:
    mode: str
    exchange: ExchangeConfig
    binance: BinanceConfig
    symbol_list: tuple[str, ...]
    enabled_symbols: tuple[str, ...]
    symbol_configs: dict[str, SymbolTradingConfig]
    polling_interval_seconds: int
    logging_level: str
    paper_initial_cash: float
    paper_state_file: str
    paper_trade_log_file: str
    fixed_order_quote_amount: float
    cash_usage_pct: float
    max_positions: int
    stop_loss_pct: float
    take_profit_pct: float
    max_single_order_usdt: float
    max_consecutive_losing_trades: int
    max_consecutive_errors: int
    runtime_state_file: str
    robot_initial_status: str
    status_file: str
    system_log_file: str
    trade_log_file: str
    error_log_file: str
    allow_live_trading: bool
    live_execute_enabled: bool
    require_manual_confirm: bool
    live_enabled: bool


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

    if current_text.startswith("- "):
        items: list[Any] = []
        while index < len(lines):
            current_indent, current_text = lines[index]
            if current_indent < indent:
                break
            if current_indent != indent or not current_text.startswith("- "):
                raise ValueError(f"Invalid list item near '{current_text}'")

            item_text = current_text[2:].strip()
            index += 1
            if item_text:
                items.append(_parse_scalar(item_text))
                continue

            nested_indent = lines[index][0] if index < len(lines) else indent
            nested_value, index = _parse_block(lines, index, nested_indent)
            items.append(nested_value)
        return items, index

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


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    parsed, _ = _parse_block(_normalize_lines(path.read_text(encoding="utf-8")), 0, 0)
    if not isinstance(parsed, dict):
        raise ValueError(f"Top-level YAML content must be a mapping: {path}")
    return parsed


def _format_yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if all(char.isalnum() or char in {"_", ".", "/", ":", "-"} for char in text):
        return text
    return '"' + text.replace('"', '\\"') + '"'


def dump_yaml(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def write_value(value: Any, indent: int, key: str | None = None) -> None:
        prefix = " " * indent
        if isinstance(value, dict):
            if key is not None:
                lines.append(f"{prefix}{key}:")
            child_indent = indent + (2 if key is not None else 0)
            for child_key, child_value in value.items():
                write_value(child_value, child_indent, str(child_key))
            return
        if isinstance(value, list):
            if key is not None:
                lines.append(f"{prefix}{key}:")
                item_indent = indent + 2
            else:
                item_indent = indent
            for item in value:
                item_prefix = " " * item_indent
                if isinstance(item, (dict, list)):
                    lines.append(f"{item_prefix}-")
                    write_value(item, item_indent + 2)
                else:
                    lines.append(f"{item_prefix}- {_format_yaml_scalar(item)}")
            return
        if key is None:
            lines.append(f"{prefix}{_format_yaml_scalar(value)}")
        else:
            lines.append(f"{prefix}{key}: {_format_yaml_scalar(value)}")

    for top_key, top_value in data.items():
        write_value(top_value, 0, str(top_key))
    return "\n".join(lines) + "\n"


def save_symbols_config(symbols_config: dict[str, Any], symbols_path: Path | None = None) -> None:
    path = symbols_path or DEFAULT_SYMBOLS_PATH
    path.write_text(dump_yaml(symbols_config), encoding="utf-8")


def _coerce_bool(value: Any, field_name: str, symbol: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"Invalid symbols.yaml: symbols.{symbol}.{field_name} must be true or false")


def _coerce_positive_float(value: Any, field_name: str, symbol: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Invalid symbols.yaml: symbols.{symbol}.{field_name} must be a number greater than 0")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid symbols.yaml: symbols.{symbol}.{field_name} must be a number greater than 0"
        ) from exc
    if number <= 0:
        raise ValueError(f"Invalid symbols.yaml: symbols.{symbol}.{field_name} must be greater than 0")
    return number


def _coerce_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Invalid settings.yaml: {field_name} must be an integer greater than 0")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid settings.yaml: {field_name} must be an integer greater than 0") from exc
    if number <= 0:
        raise ValueError(f"Invalid settings.yaml: {field_name} must be greater than 0")
    return number


def _coerce_timeframe(value: Any, field_name: str, symbol: str) -> str:
    timeframe = str(value)
    if timeframe not in VALID_SYMBOL_TIMEFRAME_SET:
        allowed = ", ".join(VALID_SYMBOL_TIMEFRAMES)
        raise ValueError(
            f"Invalid symbols.yaml: symbols.{symbol}.{field_name} must be one of: {allowed}"
        )
    return timeframe


def _default_symbol_config(symbol: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "trend_timeframe": "4h",
        "signal_timeframe": "15m",
        "order_amount": 100,
        "max_loss_amount": 20,
        "paused_by_loss": False,
    }


def _validate_symbol_config(symbol: str, raw_config: Any) -> SymbolTradingConfig:
    if not symbol or not isinstance(symbol, str):
        raise ValueError("Invalid symbols.yaml: symbol names must be non-empty strings")
    if not isinstance(raw_config, dict):
        raise ValueError(f"Invalid symbols.yaml: symbols.{symbol} must be a mapping")

    merged_config = _default_symbol_config(symbol)
    merged_config.update(raw_config)
    return SymbolTradingConfig(
        symbol=symbol,
        enabled=_coerce_bool(merged_config.get("enabled"), "enabled", symbol),
        trend_timeframe=_coerce_timeframe(merged_config.get("trend_timeframe"), "trend_timeframe", symbol),
        signal_timeframe=_coerce_timeframe(merged_config.get("signal_timeframe"), "signal_timeframe", symbol),
        order_amount=_coerce_positive_float(merged_config.get("order_amount"), "order_amount", symbol),
        max_loss_amount=_coerce_positive_float(merged_config.get("max_loss_amount"), "max_loss_amount", symbol),
        paused_by_loss=_coerce_bool(merged_config.get("paused_by_loss"), "paused_by_loss", symbol),
    )


def load_symbols_config(symbols_path: Path | None = None) -> dict[str, Any]:
    path = symbols_path or DEFAULT_SYMBOLS_PATH
    raw_config = _load_yaml_file(path)
    raw_symbols = raw_config.get("symbols")

    if raw_symbols is None:
        raise ValueError("Invalid symbols.yaml: missing top-level 'symbols' section")

    if isinstance(raw_symbols, list):
        migrated_symbols = {}
        for raw_symbol in raw_symbols:
            symbol = str(raw_symbol)
            migrated_symbols[symbol] = _default_symbol_config(symbol)
        raw_symbols = migrated_symbols
    elif not isinstance(raw_symbols, dict):
        raise ValueError("Invalid symbols.yaml: top-level 'symbols' must be a mapping or legacy list")

    validated_symbols = {}
    for symbol, symbol_config in raw_symbols.items():
        validated = _validate_symbol_config(str(symbol), symbol_config)
        validated_symbols[validated.symbol] = {
            "enabled": validated.enabled,
            "trend_timeframe": validated.trend_timeframe,
            "signal_timeframe": validated.signal_timeframe,
            "order_amount": validated.order_amount,
            "max_loss_amount": validated.max_loss_amount,
            "paused_by_loss": validated.paused_by_loss,
        }

    if not validated_symbols:
        raise ValueError("Invalid symbols.yaml: top-level 'symbols' must contain at least one symbol")

    normalized_config = dict(raw_config)
    normalized_config["symbols"] = validated_symbols
    return normalized_config


def get_symbol_names(symbols_config: dict[str, Any]) -> tuple[str, ...]:
    symbols = symbols_config.get("symbols", {})
    if isinstance(symbols, dict):
        return tuple(symbols.keys())
    return tuple(str(symbol) for symbol in symbols)


def get_enabled_symbol_names(symbols_config: dict[str, Any]) -> tuple[str, ...]:
    symbols = symbols_config.get("symbols", {})
    if not isinstance(symbols, dict):
        return tuple(str(symbol) for symbol in symbols)
    return tuple(
        symbol
        for symbol, symbol_config in symbols.items()
        if isinstance(symbol_config, dict)
        and bool(symbol_config.get("enabled", True))
        and not bool(symbol_config.get("paused_by_loss", False))
    )


def get_symbol_trading_config(symbols_config: dict[str, Any], symbol: str) -> SymbolTradingConfig:
    symbols = symbols_config.get("symbols", {})
    if not isinstance(symbols, dict) or symbol not in symbols:
        raise ValueError(f"Invalid symbols.yaml: symbols.{symbol} is not configured")
    return _validate_symbol_config(symbol, symbols[symbol])


def get_symbol_trading_configs(symbols_config: dict[str, Any]) -> dict[str, SymbolTradingConfig]:
    symbols = symbols_config.get("symbols", {})
    if not isinstance(symbols, dict):
        return {}
    return {
        symbol: _validate_symbol_config(symbol, symbol_config)
        for symbol, symbol_config in symbols.items()
    }


def load_project_config(
    settings_path: Path | None = None,
    symbols_path: Path | None = None,
) -> dict[str, Any]:
    settings = _load_yaml_file(settings_path or DEFAULT_SETTINGS_PATH)
    symbols = load_symbols_config(symbols_path or DEFAULT_SYMBOLS_PATH)
    settings["symbols_config"] = symbols
    return settings


def load_backtest_runtime(
    settings: dict[str, Any] | None = None,
    symbol: str | None = None,
) -> BacktestRuntimeConfig:
    settings = settings or load_project_config()
    market = settings.get("market", {})
    backtest = settings.get("backtest", {})
    symbols_config = settings.get("symbols_config", {})
    symbol_files = symbols_config.get("symbol_files", {})

    symbol_list = get_symbol_names(symbols_config) or tuple(market.get("default_symbols", []))
    selected_symbol = symbol or market.get("default_symbol") or (symbol_list[0] if symbol_list else "")
    symbol_data = symbol_files.get(selected_symbol, {}).get("backtest", {})

    entry_timeframe = str(market.get("timeframe", {}).get("entry", "1h"))
    trend_timeframe = str(market.get("timeframe", {}).get("trend", "4h"))

    return BacktestRuntimeConfig(
        mode=str(settings.get("app", {}).get("mode", "backtest")),
        exchange_name=str(settings.get("exchange", {}).get("name", "binance")),
        symbol=selected_symbol,
        symbol_list=symbol_list,
        entry_timeframe=entry_timeframe,
        trend_timeframe=trend_timeframe,
        polling_interval_seconds=int(market.get("polling_interval_seconds", 60)),
        initial_capital=float(backtest.get("initial_capital", 10000.0)),
        report_file=str(backtest.get("report_file", "reports/backtest_dashboard.html")),
        log_file=str(backtest.get("log_file", "logs/backtest_events.json")),
        logging_level=str(settings.get("logging", {}).get("level", "INFO")),
        data_file_1h=str(symbol_data.get(entry_timeframe, f"data/{selected_symbol}_{entry_timeframe}.csv")),
        data_file_4h=str(symbol_data.get(trend_timeframe, f"data/{selected_symbol}_{trend_timeframe}.csv")),
    )


def load_execution_runtime(settings: dict[str, Any] | None = None) -> ExecutionRuntimeConfig:
    settings = settings or load_project_config()
    market = settings.get("market", {})
    paper = settings.get("paper", {})
    execution = settings.get("execution", {})
    safety = settings.get("safety", {})
    live = settings.get("live", {})
    binance = settings.get("binance", {})
    logging = settings.get("logging", {})
    risk = settings.get("risk", {})
    symbols_config = settings.get("symbols_config", {})
    configured_symbol_names = get_symbol_names(symbols_config)
    symbol_list = configured_symbol_names or tuple(market.get("default_symbols", []))
    configured_enabled_symbols = tuple(execution.get("enabled_symbols", ()))
    enabled_symbol_names = get_enabled_symbol_names(symbols_config)
    enabled_symbols = configured_enabled_symbols or enabled_symbol_names or symbol_list
    if configured_symbol_names:
        enabled_symbols = tuple(
            symbol
            for symbol in enabled_symbols
            if symbol in symbol_list and symbol in enabled_symbol_names
        )
    else:
        enabled_symbols = tuple(symbol for symbol in enabled_symbols if symbol in symbol_list)

    return ExecutionRuntimeConfig(
        mode=str(settings.get("app", {}).get("mode", "backtest")),
        exchange=ExchangeConfig(
            name=str(settings.get("exchange", {}).get("name", "binance")),
            base_url=str(settings.get("exchange", {}).get("base_url", "https://api.binance.com")),
            recv_window=int(settings.get("exchange", {}).get("recv_window", 5000)),
            request_timeout_seconds=int(settings.get("exchange", {}).get("request_timeout_seconds", 10)),
        ),
        binance=BinanceConfig(
            rules_cache_ttl_seconds=int(binance.get("rules_cache_ttl_seconds", 3600)),
        ),
        symbol_list=tuple(
            symbol_list
        ),
        enabled_symbols=enabled_symbols,
        symbol_configs=get_symbol_trading_configs(symbols_config),
        polling_interval_seconds=int(market.get("polling_interval_seconds", 60)),
        logging_level=str(settings.get("logging", {}).get("level", "INFO")),
        paper_initial_cash=float(paper.get("initial_cash", 10000.0)),
        paper_state_file=str(paper.get("state_file", "runtime/paper_state.json")),
        paper_trade_log_file=str(paper.get("trade_log_file", "logs/paper_trades.jsonl")),
        fixed_order_quote_amount=float(execution.get("fixed_order_quote_amount", 1000.0)),
        cash_usage_pct=float(execution.get("cash_usage_pct", 0.1)),
        max_positions=int(execution.get("max_positions", 3)),
        stop_loss_pct=float(execution.get("stop_loss_pct", 3.0)),
        take_profit_pct=float(execution.get("take_profit_pct", 6.0)),
        max_single_order_usdt=float(risk.get("max_single_order_usdt", 20.0)),
        max_consecutive_losing_trades=_coerce_positive_int(
            risk.get("max_consecutive_losing_trades", 4),
            "risk.max_consecutive_losing_trades",
        ),
        max_consecutive_errors=int(
            safety.get("max_consecutive_errors", execution.get("max_consecutive_errors", 3))
        ),
        runtime_state_file=str(execution.get("runtime_state_file", "runtime/robot_state.json")),
        robot_initial_status=str(execution.get("robot_initial_status", "running")),
        status_file=str(execution.get("status_file", "runtime/status.json")),
        system_log_file=str(logging.get("system_log_file", "logs/system.log")),
        trade_log_file=str(logging.get("trade_log_file", "logs/trade.log")),
        error_log_file=str(logging.get("error_log_file", "logs/error.log")),
        allow_live_trading=bool(safety.get("allow_live_trading", False)),
        live_execute_enabled=bool(safety.get("live_execute_enabled", False)),
        require_manual_confirm=bool(safety.get("require_manual_confirm", True)),
        live_enabled=bool(live.get("enabled", False)),
    )
