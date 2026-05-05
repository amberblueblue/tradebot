#!/bin/bash
set -u

PROD_DIR="/Users/eason/traderbot_prod"
LABEL="com.eason.traderbot.prod"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_FILE="$PROD_DIR/logs/prod_launchd.log"

mkdir -p "$PROD_DIR/logs"
exec >> "$LOG_FILE" 2>&1

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] traderbot_prod stop requested"

launchctl unload "$PLIST_TARGET" >/dev/null 2>&1 || true

find_pids() {
  local script_path="$1"
  local pids

  pids=$(pgrep -f "$script_path" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "$pids"
    return 0
  fi

  ps ax -o pid=,command= 2>/dev/null \
    | grep -F "$script_path" \
    | grep -v grep \
    | awk '{print $1}' || true
}

stop_process() {
  local name="$1"
  local script_path="$2"
  local pids

  pids=$(find_pids "$script_path")
  if [ -z "$pids" ]; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $name not running"
    return 0
  fi

  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] stopping $name pids=$(echo "$pids" | tr '\n' ' ')"
  echo "$pids" | xargs kill
  sleep 2

  pids=$(find_pids "$script_path")
  if [ -n "$pids" ]; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] force stopping $name pids=$(echo "$pids" | tr '\n' ' ')"
    echo "$pids" | xargs kill -9
  fi
}

stop_process "start_prod" "$PROD_DIR/scripts/start_prod.sh"
stop_process "run_bot" "$PROD_DIR/run_bot.py"
stop_process "web_app" "$PROD_DIR/web_app.py"

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] traderbot_prod stop done"
