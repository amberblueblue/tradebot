#!/bin/bash
set -euo pipefail

DEV_DIR="/Users/eason/traderbot_dev"
PROD_DIR="/Users/eason/traderbot_prod"
BACKUP_ROOT="$PROD_DIR/backups"

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

PROD_CONFIG_FILES=(
  "config/settings.yaml"
  "config/symbols.yaml"
  "config/futures_settings.yaml"
  "config/futures_symbols.yaml"
)

RSYNC_EXCLUDES=(
  ".env"
  ".env.*"
  "logs/"
  "data/"
  "__pycache__/"
  ".git/"
  ".DS_Store"
  "config/settings.yaml"
  "config/symbols.yaml"
  "config/futures_settings.yaml"
  "config/futures_symbols.yaml"
  ".venv/"
  "venv/"
  "env/"
  "launchd/com.eason.traderbot.prod.plist"
  "scripts/start_prod.sh"
  "scripts/stop_prod.sh"
  "scripts/install_launchd.sh"
  "scripts/uninstall_launchd.sh"
  "scripts/status_prod.sh"
  "runtime/*.pid"
  "runtime/*.json"
)

log_step() {
  echo
  echo "==> $1"
}

require_prod_config_files() {
  for config_file in "${PROD_CONFIG_FILES[@]}"; do
    if [ ! -f "$PROD_DIR/$config_file" ]; then
      echo "missing prod config file: $PROD_DIR/$config_file"
      exit 1
    fi
  done
}

print_rsync_excludes() {
  log_step "rsync exclude list"
  for exclude in "${RSYNC_EXCLUDES[@]}"; do
    echo "- $exclude"
  done
}

backup_prod_config() {
  local backup_dir
  backup_dir="$BACKUP_ROOT/prod_config_backup_$(date +%Y%m%d_%H%M%S)"

  log_step "backing up prod config"
  mkdir -p "$backup_dir/config"
  for config_file in "${PROD_CONFIG_FILES[@]}"; do
    cp "$PROD_DIR/$config_file" "$backup_dir/$config_file"
    echo "backed up $config_file"
  done
  echo "backup directory: $backup_dir"
}

if [ "$(pwd)" != "$DEV_DIR" ]; then
  echo "deploy_to_prod.sh must be run from $DEV_DIR"
  exit 1
fi

if [ ! -d "$PROD_DIR" ]; then
  echo "prod directory not found: $PROD_DIR"
  exit 1
fi

print_rsync_excludes
require_prod_config_files
backup_prod_config

log_step "checking syntax"
python3 -m py_compile "${COMPILE_TARGETS[@]}"

log_step "stopping prod"
"$PROD_DIR/scripts/stop_prod.sh"

log_step "syncing files"
RSYNC_ARGS=(-av --delete)
for exclude in "${RSYNC_EXCLUDES[@]}"; do
  RSYNC_ARGS+=(--exclude "$exclude")
done
rsync "${RSYNC_ARGS[@]}" "$DEV_DIR/" "$PROD_DIR/"

log_step "confirming prod config still exists"
require_prod_config_files

log_step "starting prod"
"$PROD_DIR/scripts/install_launchd.sh"

log_step "checking status"
sleep 3
"$PROD_DIR/scripts/status_prod.sh"

log_step "verifying production config files"
require_prod_config_files
echo "Production config files verified OK"
