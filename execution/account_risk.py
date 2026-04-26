from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from observability.event_logger import StructuredLogger
from storage.db import DEFAULT_DB_PATH, get_connection, initialize_database


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCOUNT_RISK_STATE_FILE = PROJECT_ROOT / "runtime" / "account_risk.json"
BLOCK_REASON_CONSECUTIVE_LOSSES = "consecutive_losses"


@dataclass
class AccountRiskState:
    account_risk_blocked: bool = False
    blocked_reason: str | None = None
    blocked_at: str | None = None
    consecutive_losing_trades: int = 0
    last_checked_trade_id: int | None = None
    last_reset_trade_id: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountRiskStore:
    def __init__(self, path: str | Path = DEFAULT_ACCOUNT_RISK_STATE_FILE) -> None:
        self.path = Path(path)

    def load(self) -> AccountRiskState:
        if not self.path.exists():
            return AccountRiskState()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return AccountRiskState(
            account_risk_blocked=bool(payload.get("account_risk_blocked", False)),
            blocked_reason=payload.get("blocked_reason"),
            blocked_at=payload.get("blocked_at"),
            consecutive_losing_trades=int(payload.get("consecutive_losing_trades", 0) or 0),
            last_checked_trade_id=payload.get("last_checked_trade_id"),
            last_reset_trade_id=int(payload.get("last_reset_trade_id", 0) or 0),
        )

    def save(self, state: AccountRiskState) -> AccountRiskState:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
        return state


def _latest_trade_id(db_path: str | Path = DEFAULT_DB_PATH) -> int:
    initialize_database(db_path)
    with get_connection(db_path) as connection:
        row = connection.execute("SELECT MAX(id) AS max_id FROM trades").fetchone()
    return int(row["max_id"] or 0)


def count_consecutive_losing_trades(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    after_trade_id: int = 0,
) -> tuple[int, int | None]:
    initialize_database(db_path)
    with get_connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, realized_pnl
            FROM trades
            WHERE id > ?
              AND UPPER(side) = 'SELL'
            ORDER BY id DESC
            LIMIT 200
            """,
            (int(after_trade_id),),
        ).fetchall()

    consecutive_losses = 0
    last_checked_trade_id = int(rows[0]["id"]) if rows else None
    for row in rows:
        realized_pnl = float(row["realized_pnl"] or 0.0)
        if realized_pnl < 0:
            consecutive_losses += 1
            continue
        break
    return consecutive_losses, last_checked_trade_id


def evaluate_account_risk(
    *,
    max_consecutive_losing_trades: int,
    state_file: str | Path = DEFAULT_ACCOUNT_RISK_STATE_FILE,
    db_path: str | Path = DEFAULT_DB_PATH,
    system_log_file: str = "logs/system.log",
    mode: str = "paper",
) -> AccountRiskState:
    store = AccountRiskStore(state_file)
    state = store.load()
    consecutive_losses, last_checked_trade_id = count_consecutive_losing_trades(
        db_path=db_path,
        after_trade_id=state.last_reset_trade_id,
    )
    state.consecutive_losing_trades = consecutive_losses
    state.last_checked_trade_id = last_checked_trade_id

    if state.account_risk_blocked:
        return store.save(state)

    if consecutive_losses >= max_consecutive_losing_trades:
        state.account_risk_blocked = True
        state.blocked_reason = BLOCK_REASON_CONSECUTIVE_LOSSES
        state.blocked_at = _utc_now()
        StructuredLogger(system_log_file).log(
            symbol="-",
            action="account_risk_blocked",
            reason=BLOCK_REASON_CONSECUTIVE_LOSSES,
            mode=mode,
            consecutive_losses=consecutive_losses,
        )
        print(f"[account_risk_blocked] consecutive_losses={consecutive_losses}")

    return store.save(state)


def get_account_risk_status(
    *,
    state_file: str | Path = DEFAULT_ACCOUNT_RISK_STATE_FILE,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> AccountRiskState:
    store = AccountRiskStore(state_file)
    state = store.load()
    consecutive_losses, last_checked_trade_id = count_consecutive_losing_trades(
        db_path=db_path,
        after_trade_id=state.last_reset_trade_id,
    )
    state.consecutive_losing_trades = consecutive_losses
    state.last_checked_trade_id = last_checked_trade_id
    return store.save(state)


def reset_account_risk(
    *,
    state_file: str | Path = DEFAULT_ACCOUNT_RISK_STATE_FILE,
    db_path: str | Path = DEFAULT_DB_PATH,
    system_log_file: str = "logs/system.log",
    mode: str = "paper",
) -> AccountRiskState:
    latest_trade_id = _latest_trade_id(db_path)
    state = AccountRiskState(last_reset_trade_id=latest_trade_id)
    StructuredLogger(system_log_file).log(
        symbol="-",
        action="account_risk_reset",
        reason="manual_reset",
        mode=mode,
        last_reset_trade_id=latest_trade_id,
    )
    print("[account_risk_reset]")
    return AccountRiskStore(state_file).save(state)


def account_risk_status_payload(state: AccountRiskState) -> dict[str, Any]:
    return {
        "consecutive_losing_trades": state.consecutive_losing_trades,
        "account_risk_blocked": state.account_risk_blocked,
        "blocked_reason": state.blocked_reason,
        "blocked_at": state.blocked_at,
    }
