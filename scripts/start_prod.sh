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

exec >> "$LOG_FILE" 2>&1

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] traderbot_prod launch start"
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] python3=$(command -v python3 || echo missing)"

shutdown() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] traderbot_prod launch stop requested"
  for pid_file in "$PROD_DIR/runtime/web_app.pid" "$PROD_DIR/runtime/run_bot.pid"; do
    if [ -f "$pid_file" ]; then
      pid=$(cat "$pid_file")
      if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
        echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] stopping child pid=$pid"
        kill "$pid" >/dev/null 2>&1 || true
      fi
    fi
  done
  exit 0
}

trap shutdown TERM INT

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
  } >> "$process_log"
  nohup "$PYTHON_ENV" PYTHONUNBUFFERED=1 python3 "$script_path" >> "$process_log" 2>&1 &
  echo $! > "$pid_file"
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $name pid=$!"
}

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] traderbot_prod supervisor running"

while true; do
  start_process "web_app" "$WEB_SCRIPT" "$PROD_DIR/runtime/web_app.pid" "$WEB_LOG_FILE"
  start_process "run_bot" "$BOT_SCRIPT" "$PROD_DIR/runtime/run_bot.pid" "$BOT_LOG_FILE"
  sleep 10 &
  wait $!
done
