#!/usr/bin/env bash
set -euo pipefail

PROD_DIR="/Users/eason/traderbot_prod"
DATA_DIR="$PROD_DIR/data"
RUNTIME_DIR="$PROD_DIR/runtime"
BACKUP_ROOT="$PROD_DIR/backups"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/spot_state_cleanup_$STAMP"

if [[ ! -d "$DATA_DIR" || ! -d "$RUNTIME_DIR" ]]; then
  echo "Production data/runtime directories were not found under $PROD_DIR" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR/data" "$BACKUP_DIR/runtime"

backup_file() {
  local path="$1"
  if [[ -f "$path" ]]; then
    local rel="${path#$PROD_DIR/}"
    mkdir -p "$BACKUP_DIR/$(dirname "$rel")"
    cp -p "$path" "$BACKUP_DIR/$rel"
  fi
}

backup_file "$RUNTIME_DIR/paper_state.json"
backup_file "$RUNTIME_DIR/robot_state.json"
backup_file "$RUNTIME_DIR/status.json"
backup_file "$DATA_DIR/tradebot.sqlite3"
backup_file "$DATA_DIR/tradebot.sqlite3-wal"
backup_file "$DATA_DIR/tradebot.sqlite3-shm"

find "$RUNTIME_DIR" -maxdepth 1 -type f \
  \( -name 'test_*state*.json' -o -name 'test_*status*.json' -o -name 'test_paper_*.json' \) \
  -exec sh -c 'backup_dir="$1"; shift; for path do cp -p "$path" "$backup_dir/runtime/$(basename "$path")"; done' sh "$BACKUP_DIR" {} +

python3 - "$PROD_DIR" <<'PY'
import json
import sqlite3
import sys
from pathlib import Path

prod = Path(sys.argv[1])
data_dir = prod / "data"
runtime_dir = prod / "runtime"
configured_symbols = set()

try:
    import yaml

    payload = yaml.safe_load((prod / "config" / "symbols.yaml").read_text(encoding="utf-8")) or {}
    symbols = payload.get("symbols", {})
    if isinstance(symbols, dict):
        configured_symbols = {str(symbol).upper() for symbol in symbols}
except Exception:
    configured_symbols = set()


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


paper_state = load_json(runtime_dir / "paper_state.json")
if paper_state:
    paper_state["positions"] = {}
    paper_state["orders"] = []
    write_json(runtime_dir / "paper_state.json", paper_state)

for name in ("robot_state.json", "status.json"):
    path = runtime_dir / name
    state = load_json(path)
    if not state:
        continue
    symbols = state.get("symbols")
    if isinstance(symbols, dict):
        state["symbols"] = {
            symbol: value
            for symbol, value in symbols.items()
            if str(symbol).upper() in configured_symbols
        }
    last_sync = state.get("last_sync")
    if isinstance(last_sync, dict):
        last_sync["positions"] = []
        last_sync["open_orders"] = []
        if "cash_balance" in paper_state:
            last_sync["cash_balance"] = paper_state["cash_balance"]
    write_json(path, state)

db_path = data_dir / "tradebot.sqlite3"
if db_path.exists():
    with sqlite3.connect(db_path) as connection:
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "position_snapshots" in existing_tables:
            connection.execute("DELETE FROM position_snapshots WHERE mode = 'paper'")
        if "symbol_pnl_snapshots" in existing_tables:
            connection.execute("DELETE FROM symbol_pnl_snapshots WHERE mode = 'paper'")
        if "trades" in existing_tables:
            connection.execute("DELETE FROM trades WHERE mode = 'paper'")
        connection.commit()
        connection.execute("VACUUM")
PY

find "$RUNTIME_DIR" -maxdepth 1 -type f \
  \( -name 'test_*state*.json' -o -name 'test_*status*.json' -o -name 'test_paper_*.json' \) \
  -delete

echo "Spot test state cleanup complete."
echo "Backup: $BACKUP_DIR"
