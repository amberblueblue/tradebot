#!/bin/bash
set -euo pipefail

PROD_DIR="/Users/eason/traderbot_prod"
LABEL="com.eason.traderbot.prod"
OLD_WEB_LABEL="com.eason.traderbot.web"
PLIST_SOURCE="$PROD_DIR/launchd/$LABEL.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
OLD_WEB_PLIST_TARGET="$HOME/Library/LaunchAgents/$OLD_WEB_LABEL.plist"
WEB_SCRIPT="$PROD_DIR/web_app.py"
OLD_DIR="$HOME/traderbot"
OLD_WEB_SCRIPT="$OLD_DIR/web_app.py"

mkdir -p "$HOME/Library/LaunchAgents" "$PROD_DIR/logs"

if [ ! -f "$PLIST_SOURCE" ]; then
  echo "plist source not found: $PLIST_SOURCE"
  exit 1
fi

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

kill_script() {
  local name="$1"
  local script_path="$2"
  local pids

  pids=$(find_pids "$script_path")
  if [ -z "$pids" ]; then
    echo "$name: not running"
    return 0
  fi

  echo "$name: stopping pids=$(echo "$pids" | tr '\n' ' ')"
  echo "$pids" | xargs kill || true
  sleep 2
  pids=$(find_pids "$script_path")
  if [ -n "$pids" ]; then
    echo "$name: force stopping pids=$(echo "$pids" | tr '\n' ' ')"
    echo "$pids" | xargs kill -9 || true
  fi
}

kill_stale_8000_web() {
  local pids command

  pids=$(lsof -nP -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true)
  if [ -z "$pids" ]; then
    echo "8000: no listener"
    return 0
  fi

  for pid in $pids; do
    command=$(ps -p "$pid" -o command= 2>/dev/null || true)
    if echo "$command" | grep -F "web_app.py" >/dev/null 2>&1 \
      && ! echo "$command" | grep -F "$WEB_SCRIPT" >/dev/null 2>&1; then
      echo "8000: stopping stale web pid=$pid command=$command"
      kill "$pid" >/dev/null 2>&1 || true
      sleep 1
      if kill -0 "$pid" >/dev/null 2>&1; then
        echo "8000: force stopping stale web pid=$pid"
        kill -9 "$pid" >/dev/null 2>&1 || true
      fi
    else
      echo "8000: preserving listener pid=$pid command=$command"
    fi
  done
}

echo "unloading launchd labels"
launchctl unload "$PLIST_TARGET" >/dev/null 2>&1 || true
launchctl unload "$OLD_WEB_PLIST_TARGET" >/dev/null 2>&1 || true

kill_script "old web_app.py process" "$OLD_WEB_SCRIPT"
kill_stale_8000_web

cp "$PLIST_SOURCE" "$PLIST_TARGET"
launchctl load "$PLIST_TARGET"

echo "installed and loaded $LABEL"
launchctl list | grep "$LABEL" || true
