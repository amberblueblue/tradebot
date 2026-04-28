from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_FUTURES_SETTINGS_PATH = CONFIG_DIR / "futures_settings.yaml"
DEFAULT_FUTURES_SYMBOLS_PATH = CONFIG_DIR / "futures_symbols.yaml"


@dataclass(frozen=True)
class FuturesConfig:
    settings: dict[str, Any]
    symbols: dict[str, Any]
    settings_path: Path
    symbols_path: Path


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


def load_futures_config(
    settings_path: Path = DEFAULT_FUTURES_SETTINGS_PATH,
    symbols_path: Path = DEFAULT_FUTURES_SYMBOLS_PATH,
) -> FuturesConfig:
    return FuturesConfig(
        settings=load_yaml_mapping(settings_path),
        symbols=load_yaml_mapping(symbols_path),
        settings_path=settings_path,
        symbols_path=symbols_path,
    )
