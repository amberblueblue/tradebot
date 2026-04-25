from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.bot_state import (
    AccountReconciliationSnapshot,
    BotState,
    ERROR,
    PAUSED,
    RUNNING,
    STOPPED,
    SyncSnapshot,
    VALID_BOT_STATUSES,
)


def _default_symbol_state() -> dict[str, Any]:
    return {
        "strategy_state": "IDLE",
        "entry_price": None,
        "entry_bar_index": None,
        "partial1_done": False,
        "partial2_done": False,
        "max_unrealized_return": 0.0,
        "cooldown_remaining": 0,
        "last_bar_timestamp": None,
        "last_action_bar_timestamp": None,
        "last_signal": None,
        "realized_pnl": 0.0,
        "paused_by_loss": False,
    }


class StateStore:
    def __init__(
        self,
        state_path: str,
        *,
        status_path: str,
        initial_status: str = RUNNING,
        mode: str = "paper",
        broker_name: str = "paper",
    ) -> None:
        self.state_path = Path(state_path)
        self.status_path = Path(status_path)
        self.initial_status = initial_status if initial_status in VALID_BOT_STATUSES else RUNNING
        self.mode = mode
        self.broker_name = broker_name
        self.state = self._load_state()
        self.write_status_snapshot()

    def _default_state(self) -> BotState:
        return BotState(
            robot_status=self.initial_status,
            mode=self.mode,
            broker_name=self.broker_name,
        )

    def _load_state(self) -> BotState:
        if not self.state_path.exists():
            return self._default_state()

        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        snapshot = SyncSnapshot(**payload.get("last_sync", {}))
        account_reconciliation = AccountReconciliationSnapshot(
            **payload.get("account_reconciliation", {})
        )
        state = BotState(
            robot_status=payload.get("robot_status", self.initial_status),
            mode=payload.get("mode", self.mode),
            broker_name=payload.get("broker_name", self.broker_name),
            conservative_mode=bool(payload.get("conservative_mode", False)),
            consecutive_errors=int(payload.get("consecutive_errors", 0)),
            last_error=payload.get("last_error"),
            startup_synced=bool(payload.get("startup_synced", False)),
            symbols=dict(payload.get("symbols", {})),
            last_sync=snapshot,
            account_reconciliation=account_reconciliation,
        )
        if state.robot_status not in VALID_BOT_STATUSES:
            state.robot_status = PAUSED
            state.conservative_mode = True
        return state

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.write_status_snapshot()

    def write_status_snapshot(self) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_robot_status(self) -> str:
        return self.state.robot_status

    def set_robot_status(self, status: str) -> None:
        self.state.robot_status = status if status in VALID_BOT_STATUSES else PAUSED
        self.save()

    def set_conservative_mode(self, enabled: bool) -> None:
        self.state.conservative_mode = enabled
        self.save()

    def is_conservative_mode(self) -> bool:
        return self.state.conservative_mode

    def set_sync_snapshot(self, snapshot: SyncSnapshot) -> None:
        self.state.last_sync = snapshot
        self.state.startup_synced = True
        self.save()

    def set_account_reconciliation_snapshot(self, snapshot: AccountReconciliationSnapshot) -> None:
        self.state.account_reconciliation = snapshot
        self.save()

    def get_symbol_state(self, symbol: str) -> dict[str, Any]:
        if symbol not in self.state.symbols:
            self.state.symbols[symbol] = _default_symbol_state()
            self.save()
        return self.state.symbols[symbol]

    def set_symbol_state(self, symbol: str, **updates: Any) -> dict[str, Any]:
        current = self.get_symbol_state(symbol)
        current.update(updates)
        self.save()
        return current

    def increment_error(self, error_message: str) -> int:
        self.state.consecutive_errors += 1
        self.state.last_error = error_message
        self.save()
        return self.state.consecutive_errors

    def reset_consecutive_errors(self) -> None:
        if self.state.consecutive_errors == 0 and self.state.last_error is None:
            return
        self.state.consecutive_errors = 0
        self.state.last_error = None
        self.save()

    def is_error_limit_reached(self) -> bool:
        return self.state.robot_status == ERROR

    def mark_startup_warning(self, warning: str) -> None:
        warnings = list(self.state.last_sync.warnings)
        warnings.append(warning)
        self.state.last_sync.warnings = warnings
        self.state.conservative_mode = True
        if self.state.robot_status == RUNNING:
            self.state.robot_status = PAUSED
        self.save()


RuntimeStore = StateStore

__all__ = [
    "ERROR",
    "PAUSED",
    "RUNNING",
    "STOPPED",
    "StateStore",
    "RuntimeStore",
    "SyncSnapshot",
    "AccountReconciliationSnapshot",
]
