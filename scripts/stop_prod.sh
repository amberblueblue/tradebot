#!/bin/bash
set -u

PROD_DIR="/Users/eason/traderbot_prod"
LABEL="com.eason.traderbot.prod"
OLD_WEB_LABEL="com.eason.traderbot.web"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
OLD_WEB_PLIST_TARGET="$HOME/Library/LaunchAgents/$OLD_WEB_LABEL.plist"
LOG_FILE="$PROD_DIR/logs/prod_launchd.log"
WEB_SCRIPT="$PROD_DIR/web_app.py"
BOT_SCRIPT="$PROD_DIR/run_bot.py"
START_SCRIPT="$PROD_DIR/scripts/start_prod.sh"
OLD_DIR="$HOME/traderbot"
OLD_WEB_SCRIPT="$OLD_DIR/web_app.py"
OLD_BOT_SCRIPT="$OLD_DIR/run_bot.py"

mkdir -p "$PROD_DIR/logs"
exec >> "$LOG_FILE" 2>&1

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] traderbot_prod stop requested"

launchctl unload "$PLIST_TARGET" >/dev/null 2>&1 || true
launchctl unload "$OLD_WEB_PLIST_TARGET" >/dev/null 2>&1 || true

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

stop_process "start_prod" "$START_SCRIPT"
stop_process "run_bot" "$BOT_SCRIPT"
stop_process "web_app" "$WEB_SCRIPT"
stop_process "old run_bot" "$OLD_BOT_SCRIPT"
stop_process "old web_app" "$OLD_WEB_SCRIPT"

stop_stale_8000_web() {
  local pids

  pids=$(lsof -nP -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true)
  if [ -z "$pids" ]; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] no 8000 listener"
    return 0
  fi

  for pid in $pids; do
    command=$(ps -p "$pid" -o command= 2>/dev/null || true)
    if echo "$command" | grep -F "web_app.py" >/dev/null 2>&1 \
      && ! echo "$command" | grep -F "$WEB_SCRIPT" >/dev/null 2>&1; then
      echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] stopping stale 8000 web pid=$pid command=$command"
      kill "$pid" >/dev/null 2>&1 || true
      sleep 1
      if kill -0 "$pid" >/dev/null 2>&1; then
        echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] force stopping stale 8000 web pid=$pid"
        kill -9 "$pid" >/dev/null 2>&1 || true
      fi
    else
      echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] preserving 8000 listener pid=$pid command=$command"
    fi
  done
}

stop_stale_8000_web

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] traderbot_prod stop done"
