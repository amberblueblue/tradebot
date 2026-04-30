#!/bin/bash
set -euo pipefail

TEST_DIR="/Users/eason/traderbot_test"
PROD_DIR="/Users/eason/traderbot_prod"

COMPILE_TARGETS=(
  "web_app.py"
  "futures_bot/run_futures_bot.py"
  "run_bot.py"
)

log_step() {
  echo
  echo "==> $1"
}

if [ "$(pwd)" != "$TEST_DIR" ]; then
  echo "deploy_test_to_prod.sh must be run from $TEST_DIR"
  exit 1
fi

if [ ! -d "$PROD_DIR" ]; then
  echo "prod directory not found: $PROD_DIR"
  exit 1
fi

log_step "checking test syntax"
python3 -m py_compile "${COMPILE_TARGETS[@]}"

log_step "stopping prod"
if [ -x "$PROD_DIR/scripts/stop_prod.sh" ]; then
  "$PROD_DIR/scripts/stop_prod.sh"
else
  echo "missing executable: $PROD_DIR/scripts/stop_prod.sh"
  exit 1
fi

log_step "syncing test to prod"
rsync -av --delete \
  --exclude ".env" \
  --exclude ".env.*" \
  --exclude "logs/" \
  --exclude "data/" \
  --exclude ".git/" \
  --exclude ".venv/" \
  --exclude "venv/" \
  --exclude "env/" \
  --exclude "__pycache__/" \
  --exclude ".DS_Store" \
  --exclude "config/settings.yaml" \
  --exclude "config/symbols.yaml" \
  --exclude "config/futures_settings.yaml" \
  --exclude "config/futures_symbols.yaml" \
  --exclude "runtime/*.pid" \
  "$TEST_DIR/" "$PROD_DIR/"

log_step "starting prod"
if [ -x "$PROD_DIR/scripts/install_launchd.sh" ]; then
  "$PROD_DIR/scripts/install_launchd.sh"
elif [ -x "$PROD_DIR/scripts/start_prod.sh" ]; then
  "$PROD_DIR/scripts/start_prod.sh"
else
  echo "missing executable: install_launchd.sh or start_prod.sh in prod scripts"
  exit 1
fi

log_step "checking prod status"
sleep 3
if [ -x "$PROD_DIR/scripts/status_prod.sh" ]; then
  "$PROD_DIR/scripts/status_prod.sh"
else
  echo "missing executable: $PROD_DIR/scripts/status_prod.sh"
  exit 1
fi
