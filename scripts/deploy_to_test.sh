#!/bin/bash
set -euo pipefail

DEV_DIR="/Users/eason/traderbot_dev"
TEST_DIR="/Users/eason/traderbot_test"

COMPILE_TARGETS=(
  "web_app.py"
  "futures_bot/run_futures_bot.py"
  "run_bot.py"
)

log_step() {
  echo
  echo "==> $1"
}

if [ "$(pwd)" != "$DEV_DIR" ]; then
  echo "deploy_to_test.sh must be run from $DEV_DIR"
  exit 1
fi

mkdir -p "$TEST_DIR"

log_step "syncing dev to test"
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
  "$DEV_DIR/" "$TEST_DIR/"

log_step "checking test syntax"
(
  cd "$TEST_DIR"
  python3 -m py_compile "${COMPILE_TARGETS[@]}"
)

log_step "deploy to test done"
