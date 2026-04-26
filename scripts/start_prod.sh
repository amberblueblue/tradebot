#!/bin/bash
set -u

PROD_DIR="/Users/eason/traderbot_prod"
LOG_FILE="$PROD_DIR/logs/prod_launchd.log"
WEB_LOG_FILE="$PROD_DIR/logs/prod_web.log"
BOT_LOG_FILE="$PROD_DIR/logs/prod_bot.log"
WEB_SCRIPT="$PROD_DIR/web_app.py"
BOT_SCRIPT="$PROD_DIR/run_bot.py"
PYTHON_ENV="/usr/bin/env"

mkdir -p "$PROD_DIR/logs" "$PROD_DIR/runtime"
cd "$PROD_DIR" || exit 1
export PATH="$PROD_DIR/.venv/bin:/Library/Frameworks/Python.framework/Versions/3.14/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

exec > >("$PYTHON_ENV" python3 -m observability.event_logger --rotate-stream "$LOG_FILE") 2>&1

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] traderbot_prod launch start"
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] python3=$(command -v python3 || echo missing)"
STARTED_PIDS=""

is_running() {
  local script_path="$1"
  pgrep -f "$script_path" >/dev/null 2>&1 && return 0
  ps ax -o command= 2>/dev/null | grep -F "$script_path" | grep -v grep >/dev/null 2>&1
}

start_process() {
  local name="$1"
  local script_path="$2"
  local pid_file="$3"
  local process_log="$4"

  if is_running "$script_path"; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $name already running"
    return 0
  fi

  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] starting $name log=$process_log"
  {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] starting $name"
    echo "working_directory=$PROD_DIR"
    echo "script=$script_path"
  } | "$PYTHON_ENV" python3 -m observability.event_logger --rotate-stream "$process_log"
  nohup bash -c 'PYTHONUNBUFFERED=1 "$1" python3 "$2" 2>&1 | "$1" python3 -m observability.event_logger --rotate-stream "$3"' _ "$PYTHON_ENV" "$script_path" "$process_log" &
  echo $! > "$pid_file"
  STARTED_PIDS="$STARTED_PIDS $!"
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $name pid=$!"
}

start_process "web_app" "$WEB_SCRIPT" "$PROD_DIR/runtime/web_app.pid" "$WEB_LOG_FILE"
start_process "run_bot" "$BOT_SCRIPT" "$PROD_DIR/runtime/run_bot.pid" "$BOT_LOG_FILE"

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] traderbot_prod launch done"

if [ -n "${STARTED_PIDS// }" ]; then
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] waiting for child pids:$STARTED_PIDS"
  wait $STARTED_PIDS
fi
