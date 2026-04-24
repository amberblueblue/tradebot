from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from collections import deque

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config.loader import load_execution_runtime, load_project_config
from observability.event_logger import LogRouter
from runtime.bot_state import PAUSED, RUNNING, STOPPED
from runtime.state import RuntimeStore, build_runtime_state


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "ui" / "templates"
STATIC_DIR = BASE_DIR / "ui" / "static"
LOG_FILE_MAP = {
    "system": BASE_DIR / "logs" / "system.log",
    "trade": BASE_DIR / "logs" / "trade.log",
    "error": BASE_DIR / "logs" / "error.log",
}


app = FastAPI(title="TraderBot Console")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _read_runtime_status(status_file: str) -> dict:
    path = Path(status_file)
    if not path.exists():
        return {
            "robot_status": "unknown",
            "conservative_mode": False,
            "startup_synced": False,
            "symbols": {},
            "last_sync": {
                "cash_balance": 0.0,
                "positions": [],
                "open_orders": [],
                "enabled_symbols": [],
                "warnings": ["runtime_status_not_found"],
                "synced_at": None,
            },
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _dashboard_context() -> dict:
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    runtime_status = _read_runtime_status(execution_config.status_file)
    return {
        "project_name": "TraderBot Local Console",
        "mode": execution_config.mode,
        "bot_status": runtime_status.get("robot_status", "unknown"),
        "enabled_symbols": list(execution_config.enabled_symbols),
        "current_time": datetime.now(timezone.utc),
        "runtime_status": runtime_status,
        "settings": settings,
    }


def _read_last_lines(path: Path, line_count: int = 100) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=line_count)]


def _load_config_view() -> dict:
    try:
        settings = load_project_config()
    except Exception as exc:
        return {
            "config_error": str(exc),
            "settings_view": None,
            "symbols_view": None,
        }

    settings_view = {
        "app_mode": settings.get("app", {}).get("mode"),
        "exchange_name": settings.get("exchange", {}).get("name"),
        "default_symbol": settings.get("market", {}).get("default_symbol"),
        "default_symbols": settings.get("market", {}).get("default_symbols", []),
        "enabled_symbols": settings.get("execution", {}).get("enabled_symbols", []),
        "polling_interval_seconds": settings.get("market", {}).get("polling_interval_seconds"),
        "risk": settings.get("risk", {}),
    }
    symbols_view = settings.get("symbols_config", {})
    return {
        "config_error": None,
        "settings_view": settings_view,
        "symbols_view": symbols_view,
    }


def _runtime_store():
    settings = load_project_config()
    execution_config = load_execution_runtime(settings)
    runtime_state = build_runtime_state(execution_config)
    store = RuntimeStore(
        execution_config.runtime_state_file,
        status_path=execution_config.status_file,
        initial_status=execution_config.robot_initial_status,
        mode=execution_config.mode,
        broker_name=runtime_state.broker_name,
    )
    logger = LogRouter(
        system_log=execution_config.system_log_file,
        trade_log=execution_config.trade_log_file,
        error_log=execution_config.error_log_file,
        mode=execution_config.mode,
    )
    return execution_config, store, logger


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    context = _dashboard_context()
    context["request"] = request
    return templates.TemplateResponse(request, "dashboard.html", context)


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, log_type: str = "system"):
    selected_type = log_type if log_type in LOG_FILE_MAP else "system"
    lines = _read_last_lines(LOG_FILE_MAP[selected_type], line_count=100)
    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "project_name": "TraderBot Local Console",
            "selected_log_type": selected_type,
            "available_log_types": tuple(LOG_FILE_MAP.keys()),
            "log_lines": lines,
            "log_exists": LOG_FILE_MAP[selected_type].exists(),
        },
    )


@app.get("/config", response_class=HTMLResponse)
def config_page(request: Request):
    context = _load_config_view()
    context.update(
        {
            "project_name": "TraderBot Local Console",
        }
    )
    return templates.TemplateResponse(request, "config.html", context)


@app.post("/bot/start")
def bot_start():
    execution_config, runtime_store, logger = _runtime_store()
    if execution_config.mode == "paper":
        runtime_store.set_robot_status(RUNNING)
        runtime_store.set_conservative_mode(False)
        logger.log_system(symbol="-", action="bot_start", reason="dashboard_start")
    else:
        logger.log_system(symbol="-", action="bot_start_blocked", reason="start_allowed_only_in_paper")
    return RedirectResponse(url="/", status_code=303)


@app.post("/bot/stop")
def bot_stop():
    _, runtime_store, logger = _runtime_store()
    runtime_store.set_robot_status(STOPPED)
    logger.log_system(symbol="-", action="bot_stop", reason="dashboard_stop")
    return RedirectResponse(url="/", status_code=303)


@app.post("/bot/pause")
def bot_pause():
    _, runtime_store, logger = _runtime_store()
    runtime_store.set_robot_status(PAUSED)
    logger.log_system(symbol="-", action="bot_pause", reason="dashboard_pause")
    return RedirectResponse(url="/", status_code=303)


if __name__ == "__main__":
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000, reload=False)
