from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
DEFAULT_SYMBOLS_PATH = CONFIG_DIR / "symbols.yaml"


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
    api_key: str
    api_secret: str
    recv_window: int


@dataclass(frozen=True)
class ExecutionRuntimeConfig:
    mode: str
    exchange: ExchangeConfig
    symbol_list: tuple[str, ...]
    polling_interval_seconds: int
    logging_level: str
    paper_initial_cash: float
    paper_state_file: str
    paper_trade_log_file: str
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


def load_project_config(
    settings_path: Path | None = None,
    symbols_path: Path | None = None,
) -> dict[str, Any]:
    settings = _load_yaml_file(settings_path or DEFAULT_SETTINGS_PATH)
    symbols = _load_yaml_file(symbols_path or DEFAULT_SYMBOLS_PATH)
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

    symbol_list = tuple(symbols_config.get("symbols", market.get("default_symbols", [])))
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
    live = settings.get("live", {})
    symbols_config = settings.get("symbols_config", {})

    return ExecutionRuntimeConfig(
        mode=str(settings.get("app", {}).get("mode", "backtest")),
        exchange=ExchangeConfig(
            name=str(settings.get("exchange", {}).get("name", "binance")),
            base_url=str(settings.get("exchange", {}).get("base_url", "https://api.binance.com")),
            api_key=str(settings.get("exchange", {}).get("api_key", "")),
            api_secret=str(settings.get("exchange", {}).get("api_secret", "")),
            recv_window=int(settings.get("exchange", {}).get("recv_window", 5000)),
        ),
        symbol_list=tuple(
            symbols_config.get("symbols", market.get("default_symbols", []))
        ),
        polling_interval_seconds=int(market.get("polling_interval_seconds", 60)),
        logging_level=str(settings.get("logging", {}).get("level", "INFO")),
        paper_initial_cash=float(paper.get("initial_cash", 10000.0)),
        paper_state_file=str(paper.get("state_file", "runtime/paper_state.json")),
        paper_trade_log_file=str(paper.get("trade_log_file", "logs/paper_trades.jsonl")),
        live_enabled=bool(live.get("enabled", False)),
    )
