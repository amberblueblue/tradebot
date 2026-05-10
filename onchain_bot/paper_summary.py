from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from onchain_bot.paper_state import load_paper_state


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAPER_SUMMARY_PATH = PROJECT_ROOT / "runtime" / "onchain_paper_summary.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_date(now: datetime | None = None) -> str:
    return (now or _utc_now()).astimezone(timezone.utc).date().isoformat()


def _empty_summary(now: datetime | None = None) -> dict[str, Any]:
    return {
        "date": _today_date(now),
        "loop_count": 0,
        "signal_counts": {
            "LONG": 0,
            "CLOSE": 0,
            "HOLD": 0,
            "ERROR": 0,
        },
        "paper_actions": {
            "open": 0,
            "close": 0,
            "skipped": 0,
        },
        "blocks": {
            "quote_risk": 0,
            "session": 0,
            "trade_limits": 0,
            "safety": 0,
            "mapping_disabled": 0,
        },
        "positions_count": 0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "updated_at": None,
    }


def _normalize_counter_map(value: Any, defaults: dict[str, int]) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    result: dict[str, int] = {}
    for key, default in defaults.items():
        raw_value = source.get(key, default)
        result[key] = raw_value if isinstance(raw_value, int) and raw_value >= 0 else default
    return result


def load_paper_summary(path: Path = DEFAULT_PAPER_SUMMARY_PATH, *, now: datetime | None = None) -> dict[str, Any]:
    today = _today_date(now)
    if not path.exists():
        return _empty_summary(now)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_summary(now)
    if not isinstance(payload, dict) or payload.get("date") != today:
        return _empty_summary(now)
    summary = _empty_summary(now)
    summary.update(payload)
    summary["loop_count"] = int(summary.get("loop_count", 0) or 0)
    summary["signal_counts"] = _normalize_counter_map(summary.get("signal_counts"), summary["signal_counts"])
    summary["paper_actions"] = _normalize_counter_map(summary.get("paper_actions"), summary["paper_actions"])
    summary["blocks"] = _normalize_counter_map(summary.get("blocks"), summary["blocks"])
    return summary


def save_paper_summary(summary: dict[str, Any], path: Path = DEFAULT_PAPER_SUMMARY_PATH) -> dict[str, Any]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "error": "onchain_paper_summary_write_failed",
            "message": str(exc),
            "path": str(path),
        }
    return {"ok": True, "path": str(path)}


def _signal_bucket(action: Any) -> str | None:
    normalized = str(action or "").upper()
    if normalized == "LONG":
        return "LONG"
    if normalized.startswith("CLOSE"):
        return "CLOSE"
    if normalized == "HOLD":
        return "HOLD"
    if normalized and normalized != "NONE":
        return "ERROR"
    return None


def _increment_signal_counts(summary: dict[str, Any], result: dict[str, Any]) -> None:
    seen_symbols: set[str] = set()
    for item in result.get("symbols", []) if isinstance(result.get("symbols"), list) else []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "")
        bucket = _signal_bucket(item.get("futures_signal"))
        if bucket is None:
            continue
        seen_symbols.add(symbol)
        summary["signal_counts"][bucket] += 1

    for action in result.get("actions", []) if isinstance(result.get("actions"), list) else []:
        if not isinstance(action, dict):
            continue
        symbol = str(action.get("symbol") or "")
        if symbol in seen_symbols:
            continue
        bucket = _signal_bucket(action.get("signal_action"))
        if bucket is not None:
            summary["signal_counts"][bucket] += 1


def _increment_action_counts(summary: dict[str, Any], result: dict[str, Any]) -> None:
    actions = result.get("actions", [])
    if not isinstance(actions, list):
        return
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_name = str(action.get("action") or "")
        reason = str(action.get("reason") or "")
        if action_name == "paper_open":
            summary["paper_actions"]["open"] += 1
        elif action_name == "paper_close":
            summary["paper_actions"]["close"] += 1
        else:
            summary["paper_actions"]["skipped"] += 1
        readiness_reasons = action.get("readiness_reasons", [])
        if not isinstance(readiness_reasons, list):
            readiness_reasons = []
        if reason == "risk_failed" or any(
            item in {"risk_failed", "quote_error", "quote_stale", "buy_quote_not_available", "sell_quote_not_available"}
            for item in readiness_reasons
        ):
            summary["blocks"]["quote_risk"] += 1
        elif reason == "outside_us_regular_session" or "outside_us_regular_session" in readiness_reasons:
            summary["blocks"]["session"] += 1
        elif reason == "trade_limit_failed":
            summary["blocks"]["trade_limits"] += 1
        elif reason == "onchain_safety_blocked":
            summary["blocks"]["safety"] += 1
        elif reason == "mapping_disabled":
            summary["blocks"]["mapping_disabled"] += 1


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _refresh_pnl_fields(summary: dict[str, Any]) -> None:
    state = load_paper_state()
    positions = state.get("positions", {})
    closed_trades = state.get("closed_trades", [])
    today = summary.get("date")
    unrealized_pnl = 0.0
    if isinstance(positions, dict):
        summary["positions_count"] = len(positions)
        unrealized_pnl = sum(
            _float_value(position.get("unrealized_pnl"))
            for position in positions.values()
            if isinstance(position, dict)
        )
    else:
        summary["positions_count"] = 0

    realized_pnl = 0.0
    if isinstance(closed_trades, list):
        for trade in closed_trades:
            if not isinstance(trade, dict):
                continue
            exit_time = str(trade.get("exit_time") or "")
            if exit_time[:10] == today:
                realized_pnl += _float_value(trade.get("realized_pnl"))
    summary["realized_pnl"] = realized_pnl
    summary["unrealized_pnl"] = unrealized_pnl


def update_paper_summary(run_result: dict[str, Any], path: Path = DEFAULT_PAPER_SUMMARY_PATH) -> dict[str, Any]:
    try:
        now = _utc_now()
        summary = load_paper_summary(path, now=now)
        summary["loop_count"] += 1
        _increment_signal_counts(summary, run_result)
        _increment_action_counts(summary, run_result)
        _refresh_pnl_fields(summary)
        summary["updated_at"] = now.isoformat()
        save_result = save_paper_summary(summary, path)
        if not save_result.get("ok"):
            summary["summary_error"] = save_result
        return summary
    except Exception as exc:
        return {
            **_empty_summary(),
            "summary_error": {
                "ok": False,
                "error": "onchain_paper_summary_update_failed",
                "message": str(exc),
            },
        }
