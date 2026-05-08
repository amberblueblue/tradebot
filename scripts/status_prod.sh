#!/bin/bash
set -u

PROD_DIR="/Users/eason/traderbot_prod"
LABEL="com.eason.traderbot.prod"
WEB_SCRIPT="$PROD_DIR/web_app.py"
BOT_SCRIPT="$PROD_DIR/run_bot.py"
OLD_DIR="$HOME/traderbot"
OLD_WEB_SCRIPT="$OLD_DIR/web_app.py"

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

show_process() {
  local name="$1"
  local script_path="$2"
  local pids pid command

  pids=$(find_pids "$script_path")
  if [ -z "$pids" ]; then
    echo "$name: not running"
    echo "$name path: $script_path"
    return 1
  fi

  echo "$name: running pids=$(echo "$pids" | tr '\n' ' ')"
  echo "$name path: $script_path"
  for pid in $pids; do
    command=$(ps -p "$pid" -o command= 2>/dev/null || true)
    echo "$name command[$pid]: $command"
  done
  return 0
}

printf 'launchd: '
launchctl list | grep "$LABEL" || echo "not loaded"

echo "web 8000 listener:"
lsof -nP -iTCP:8000 -sTCP:LISTEN || true

show_process "prod web_app.py process" "$WEB_SCRIPT" || true
show_process "prod run_bot.py process" "$BOT_SCRIPT" || true

old_pids=$(find_pids "$OLD_WEB_SCRIPT")
if [ -n "$old_pids" ]; then
  echo "old web_app.py process: running pids=$(echo "$old_pids" | tr '\n' ' ')"
else
  echo "old web_app.py process: not running"
fi

echo "logs:"
echo "  web: $PROD_DIR/logs/prod_web.log"
echo "  bot: $PROD_DIR/logs/prod_bot.log"
echo "  launchd: $PROD_DIR/logs/prod_launchd.log"
