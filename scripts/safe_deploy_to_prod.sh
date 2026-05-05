#!/bin/bash
set -euo pipefail

DEV_DIR="/Users/eason/traderbot_dev"
PROD_DIR="/Users/eason/traderbot_prod"
DEPLOY_SCRIPT="$DEV_DIR/scripts/deploy_to_prod.sh"
STOP_SCRIPT="$PROD_DIR/scripts/stop_prod.sh"
START_SCRIPT="$PROD_DIR/scripts/install_launchd.sh"
STATUS_SCRIPT="$PROD_DIR/scripts/status_prod.sh"

log_step() {
  echo
  echo "==> $1"
}

require_executable() {
  local script_path="$1"
  if [ ! -x "$script_path" ]; then
    echo "missing executable script: $script_path"
    exit 1
  fi
}

if [ "$(pwd)" != "$DEV_DIR" ]; then
  echo "safe_deploy_to_prod.sh must be run from $DEV_DIR"
  exit 1
fi

if [ ! -d "$PROD_DIR" ]; then
  echo "prod directory not found: $PROD_DIR"
  exit 1
fi

require_executable "$STOP_SCRIPT"
require_executable "$DEPLOY_SCRIPT"
require_executable "$START_SCRIPT"
require_executable "$STATUS_SCRIPT"

log_step "stopping prod"
"$STOP_SCRIPT"

log_step "verifying stopped"
if pgrep -f "$PROD_DIR" >/dev/null 2>&1; then
  echo "production processes still running:"
  pgrep -af "$PROD_DIR" || true
  exit 1
fi
echo "production processes stopped"

log_step "deploying"
"$DEPLOY_SCRIPT"

log_step "starting prod"
"$START_SCRIPT"

log_step "checking status"
STATUS_OUTPUT="$("$STATUS_SCRIPT" 2>&1)"
echo "$STATUS_OUTPUT"

if ! echo "$STATUS_OUTPUT" | grep -q "prod web_app.py process: running"; then
  echo "warning: web_app.py is not running"
fi

if ! echo "$STATUS_OUTPUT" | grep -q "prod run_bot.py process: running"; then
  echo "warning: run_bot.py is not running"
fi

echo "Futures bot is not managed by launchd; start manually if needed"
