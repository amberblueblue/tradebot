from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from config.loader import ExecutionRuntimeConfig
from config.secrets import load_binance_readonly_credentials
from exchange.binance_client import BinanceClient, BinancePrivateReadOnlyAPIError
from execution.broker import Broker
from observability.event_logger import LogRouter
from runtime.bot_state import AccountReconciliationSnapshot, PAUSED, SyncSnapshot


def _balance_total(balance: dict[str, Any]) -> float:
    try:
        free = float(balance.get("free", 0) or 0)
        locked = float(balance.get("locked", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return free + locked


def _nonzero_asset_rows(balances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for balance in balances:
        asset = str(balance.get("asset", "")).strip()
        total = _balance_total(balance)
        if not asset or total <= 0:
            continue
        rows.append(
            {
                "asset": asset,
                "free": float(balance.get("free", 0) or 0),
                "locked": float(balance.get("locked", 0) or 0),
                "total": total,
            }
        )
    return sorted(rows, key=lambda row: row["asset"])


def startup_account_reconciliation(
    *,
    execution_config: ExecutionRuntimeConfig,
    runtime_store,
    logger: LogRouter,
) -> AccountReconciliationSnapshot:
    credentials = load_binance_readonly_credentials()
    if not credentials.configured:
        snapshot = AccountReconciliationSnapshot(
            configured=False,
            query_ok=False,
            status="not_configured",
            warnings=[],
            error=None,
            checked_at=None,
        )
        runtime_store.set_account_reconciliation_snapshot(snapshot)
        return snapshot

    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        client = BinanceClient(
            base_url=execution_config.exchange.base_url,
            timeout=execution_config.exchange.request_timeout_seconds,
            error_log_file=execution_config.error_log_file,
            recv_window=execution_config.exchange.recv_window,
            credentials=credentials,
        )
        balances = client.get_account_balances()
        open_orders = client.get_open_orders()
        nonzero_assets = _nonzero_asset_rows(balances)
        warnings = []
        if nonzero_assets:
            warnings.append("real_account_nonzero_assets")
        if open_orders:
            warnings.append("real_account_open_orders")

        snapshot = AccountReconciliationSnapshot(
            configured=True,
            query_ok=True,
            status="warning" if warnings else "ok",
            warnings=warnings,
            error=None,
            nonzero_assets=nonzero_assets,
            open_orders=open_orders,
            checked_at=checked_at,
        )
        runtime_store.set_account_reconciliation_snapshot(snapshot)
        logger.log_system(
            symbol="-",
            action="startup_account_reconciliation",
            reason=snapshot.status,
            snapshot=asdict(snapshot),
        )
        return snapshot
    except BinancePrivateReadOnlyAPIError as exc:
        error_message = str(exc)
    except Exception as exc:
        error_message = f"startup_account_reconciliation_failed: {exc}"
        logger.log_error(
            symbol="-",
            action="startup_account_reconciliation_error",
            reason=error_message,
        )

    snapshot = AccountReconciliationSnapshot(
        configured=True,
        query_ok=False,
        status="failed",
        warnings=["account_reconciliation_failed"],
        error=error_message,
        checked_at=checked_at,
    )
    runtime_store.set_account_reconciliation_snapshot(snapshot)
    logger.log_system(
        symbol="-",
        action="startup_account_reconciliation_warning",
        reason=error_message,
        snapshot=asdict(snapshot),
    )
    return snapshot


def startup_sync(
    *,
    broker: Broker,
    execution_config: ExecutionRuntimeConfig,
    runtime_store,
    logger: LogRouter,
) -> SyncSnapshot:
    warnings: list[str] = []

    try:
        cash_balance = broker.get_cash_balance()
        positions = [asdict(position) for position in broker.get_positions()]
        open_orders = broker.get_open_orders()
    except Exception as exc:
        cash_balance = 0.0
        positions = []
        open_orders = []
        warnings.append(f"startup_sync_failed: {exc}")

    if not execution_config.enabled_symbols:
        warnings.append("no_enabled_symbols")

    if execution_config.mode == "live" and not execution_config.live_enabled:
        warnings.append("live_mode_not_enabled")

    snapshot = SyncSnapshot(
        cash_balance=float(cash_balance),
        positions=positions,
        open_orders=open_orders,
        enabled_symbols=list(execution_config.enabled_symbols),
        warnings=warnings,
    )
    runtime_store.set_sync_snapshot(snapshot)

    for position in positions:
        runtime_store.get_symbol_state(position["symbol"])

    if warnings:
        runtime_store.set_conservative_mode(True)
        if runtime_store.get_robot_status() == "running":
            runtime_store.set_robot_status(PAUSED)
        for warning in warnings:
            logger.log_system(symbol="-", action="startup_warning", reason=warning, snapshot=asdict(snapshot))
    else:
        runtime_store.set_conservative_mode(False)
        logger.log_system(symbol="-", action="startup_sync", reason="startup_sync_ok", snapshot=asdict(snapshot))

    return snapshot
