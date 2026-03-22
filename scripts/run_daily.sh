#!/usr/bin/env bash
# Overlap-protected wrapper for the daily orchestrator run.
# Uses flock to ensure only one instance runs at a time.
# If a previous run is still going, this exits immediately.
#
# Usage (cron):
#   30 3 * * *  /home/beck/Desktop/projects/marjon/scripts/run_daily.sh
#
# Usage (manual, pass-through args):
#   ./scripts/run_daily.sh --dry-run

set -euo pipefail

LOCK_FILE="/tmp/marjon_daily.lock"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/daily_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date -Iseconds) Daily run already in progress, skipping." >&2
    exit 0
fi

cd "$PROJECT_DIR"
source venv/bin/activate
python manage.py orchestrate --universe u001 --steps discovery,pool_mapping,ohlcv,holders "$@" 2>&1 | tee "$LOG_FILE"
