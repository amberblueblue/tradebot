import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


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

    def log(self, **payload):
        event = {
            "timestamp": payload.pop("timestamp", datetime.now(timezone.utc).isoformat()),
            **payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
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
