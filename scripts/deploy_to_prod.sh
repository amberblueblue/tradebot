#!/bin/bash
set -euo pipefail

DEV_DIR="/Users/eason/traderbot_dev"
PROD_DIR="/Users/eason/traderbot_prod"

COMPILE_TARGETS=(
  "run_bot.py"
  "web_app.py"
  "status.py"
  "config/loader.py"
  "exchange/binance_client.py"
  "exchange/rules.py"
  "execution/trader.py"
  "execution/paper_broker.py"
  "execution/live_broker.py"
  "execution/order_validator.py"
  "runtime/state.py"
  "runtime/state_store.py"
  "runtime/sync.py"
  "storage/db.py"
  "storage/repository.py"
  "observability/event_logger.py"
)

log_step() {
  echo
  echo "==> $1"
}

if [ "$(pwd)" != "$DEV_DIR" ]; then
  echo "deploy_to_prod.sh must be run from $DEV_DIR"
  exit 1
fi

if [ ! -d "$PROD_DIR" ]; then
  echo "prod directory not found: $PROD_DIR"
  exit 1
fi

log_step "checking syntax"
python3 -m py_compile "${COMPILE_TARGETS[@]}"

log_step "stopping prod"
"$PROD_DIR/scripts/stop_prod.sh"

log_step "syncing files"
rsync -av --delete \
  --exclude ".env" \
  --exclude ".env.*" \
  --exclude "logs/" \
  --exclude "data/tradebot.sqlite3" \
  --exclude ".venv/" \
  --exclude "venv/" \
  --exclude "env/" \
  --exclude "__pycache__/" \
  --exclude ".git/" \
  --exclude ".DS_Store" \
  --exclude "launchd/com.eason.traderbot.prod.plist" \
  --exclude "scripts/start_prod.sh" \
  --exclude "scripts/stop_prod.sh" \
  --exclude "scripts/install_launchd.sh" \
  --exclude "scripts/uninstall_launchd.sh" \
  --exclude "scripts/status_prod.sh" \
  --exclude "runtime/*.pid" \
  --exclude "runtime/*.json" \
  "$DEV_DIR/" "$PROD_DIR/"

log_step "starting prod"
"$PROD_DIR/scripts/install_launchd.sh"

log_step "checking status"
sleep 3
"$PROD_DIR/scripts/status_prod.sh"
