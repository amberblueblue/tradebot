#!/bin/bash
set -euo pipefail

PROD_DIR="/Users/eason/traderbot_prod"
BACKUP_ROOT="$PROD_DIR/backups"
BACKUP_DIR="$BACKUP_ROOT/symbol_reset_$(date +%Y%m%d_%H%M%S)"
SPOT_SYMBOLS="$PROD_DIR/config/symbols.yaml"
FUTURES_SYMBOLS="$PROD_DIR/config/futures_symbols.yaml"

log_step() {
  echo
  echo "==> $1"
}

require_file() {
  local file_path="$1"
  if [ ! -f "$file_path" ]; then
    echo "missing required file: $file_path"
    exit 1
  fi
}

if [ ! -d "$PROD_DIR" ]; then
  echo "prod directory not found: $PROD_DIR"
  exit 1
fi

require_file "$SPOT_SYMBOLS"
require_file "$FUTURES_SYMBOLS"

log_step "backing up production symbol configs"
mkdir -p "$BACKUP_DIR/config"
cp "$SPOT_SYMBOLS" "$BACKUP_DIR/config/symbols.yaml"
cp "$FUTURES_SYMBOLS" "$BACKUP_DIR/config/futures_symbols.yaml"
echo "backup directory: $BACKUP_DIR"

log_step "resetting spot symbols"
cat > "$SPOT_SYMBOLS" <<'YAML'
symbols:
  BTCUSDT:
    enabled: false
    order_amount: 10
    max_loss_amount: 20
    trend_timeframe: "4h"
    signal_timeframe: "15m"
    paused_by_loss: false
symbol_files:
YAML

log_step "resetting futures symbols"
cat > "$FUTURES_SYMBOLS" <<'YAML'
symbols:
  BTCUSDT:
    enabled: false
    strategy: trend_long
    leverage: 1
    margin_amount: 5
    trend_timeframe: "4h"
    signal_timeframe: "15m"
    market_session_filter: "none"
YAML

echo "Spot symbols reset to BTCUSDT disabled"
echo "Futures symbols reset to BTCUSDT disabled"
