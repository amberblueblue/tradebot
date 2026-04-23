import json
import os
from copy import deepcopy


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
