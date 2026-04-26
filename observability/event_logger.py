import json
import logging
import os
import sys
from copy import deepcopy
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_ROTATION_MAX_BYTES = 5 * 1024 * 1024
LOG_ROTATION_BACKUP_COUNT = 5
LOG_ROTATION_ENCODING = "utf-8"

EVENT_FIELDS = [
    "timestamp",
    "bar_index",
    "event_type",
    "symbol",
    "side",
    "price",
    "ema44_at_signal",
    "ema144_4h_at_signal",
    "atr_at_signal",
    "macd_line_at_signal",
    "macd_signal_at_signal",
    "macd_hist_at_signal",
    "rsi_at_signal",
    "swing_structure_state",
    "cooldown_remaining",
    "signal_rejected_reason",
    "trend_status",
    "reason_code",
    "exit_type",
    "exit_action",
    "sell_pct",
    "current_return",
    "mfe",
    "entry_price",
    "exit_price",
    "pnl",
    "holding_bars",
    "exit_reason",
]

_ROTATING_LOGGERS = {}


def _serialize_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


class EventLogger:
    def __init__(self):
        self.events = []

    def log_event(self, **kwargs):
        event = {field: None for field in EVENT_FIELDS}
        event.update(kwargs)
        serialized_event = {
            key: _serialize_value(value) for key, value in event.items() if key in EVENT_FIELDS
        }
        self.events.append(serialized_event)
        return serialized_event

    def save_logs(self, filepath):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(self.events, file, ensure_ascii=False, indent=2)
        return filepath

    def get_events(self):
        return deepcopy(self.events)


class StructuredLogger:
    def __init__(self, filepath: str):
        self.path = Path(filepath)
        self.logger = configure_rotating_file_logger(self.path)

    def log(self, **payload):
        event = {
            "timestamp": payload.pop("timestamp", datetime.now(timezone.utc).isoformat()),
            **payload,
        }
        self.logger.info(json.dumps(event, ensure_ascii=False))
        return event


class LogRouter:
    def __init__(self, *, system_log: str, trade_log: str, error_log: str, mode: str):
        self.mode = mode
        self.system = StructuredLogger(system_log)
        self.trade = StructuredLogger(trade_log)
        self.error = StructuredLogger(error_log)

    def log_system(self, *, symbol: str = "-", action: str, reason: str, **extra):
        return self.system.log(symbol=symbol, action=action, reason=reason, mode=self.mode, **extra)

    def log_trade(self, *, symbol: str, action: str, reason: str, **extra):
        return self.trade.log(symbol=symbol, action=action, reason=reason, mode=self.mode, **extra)

    def log_error(self, *, symbol: str = "-", action: str, reason: str, **extra):
        return self.error.log(symbol=symbol, action=action, reason=reason, mode=self.mode, **extra)


def configure_rotating_file_logger(
    filepath: str | Path,
    *,
    max_bytes: int = LOG_ROTATION_MAX_BYTES,
    backup_count: int = LOG_ROTATION_BACKUP_COUNT,
) -> logging.Logger:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path = path.resolve()
    logger_name = f"traderbot.rotating.{resolved_path}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    existing_handler = next(
        (
            handler
            for handler in logger.handlers
            if isinstance(handler, RotatingFileHandler)
            and Path(handler.baseFilename) == resolved_path
            and handler.maxBytes == max_bytes
            and handler.backupCount == backup_count
        ),
        None,
    )
    if existing_handler is not None and len(logger.handlers) == 1:
        _ROTATING_LOGGERS[resolved_path] = logger
        return logger

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handler = RotatingFileHandler(
        resolved_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding=LOG_ROTATION_ENCODING,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    _ROTATING_LOGGERS[resolved_path] = logger
    return logger


def stream_to_rotating_log(filepath: str | Path) -> None:
    logger = configure_rotating_file_logger(filepath)
    for line in sys.stdin:
        logger.info(line.rstrip("\n"))


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--rotate-stream":
        stream_to_rotating_log(sys.argv[2])
        return 0
    print("usage: python3 -m observability.event_logger --rotate-stream <log_file>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
