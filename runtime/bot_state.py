from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


STOPPED = "stopped"
RUNNING = "running"
PAUSED = "paused"
ERROR = "error"
VALID_BOT_STATUSES = {STOPPED, RUNNING, PAUSED, ERROR}


@dataclass
class SyncSnapshot:
    cash_balance: float = 0.0
    positions: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    enabled_symbols: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    synced_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class BotState:
    robot_status: str = RUNNING
    mode: str = "paper"
    broker_name: str = "paper"
    conservative_mode: bool = False
    consecutive_errors: int = 0
    last_error: str | None = None
    startup_synced: bool = False
    symbols: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_sync: SyncSnapshot = field(default_factory=SyncSnapshot)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
