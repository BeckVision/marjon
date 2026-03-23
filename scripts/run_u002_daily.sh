#!/usr/bin/env bash
# Daily U-002 update: fetch latest klines, metrics, and funding rate.
# Uses flock to ensure only one instance runs at a time.
#
# Usage (cron):
#   0 7 * * *  /home/beck/Desktop/projects/marjon/scripts/run_u002_daily.sh
#
# Runs at 07:00 UTC — after Binance publishes previous day's CSVs (~06:00 UTC).
# Each step fetches 1 day (self-limiting), advances watermarks.

set -euo pipefail

LOCK_FILE="/tmp/marjon_u002_daily.lock"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/u002_daily_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date -Iseconds) U-002 daily run already in progress, skipping." >&2
    exit 0
fi

cd "$PROJECT_DIR"
source venv/bin/activate
python manage.py orchestrate --universe u002 --loops 1 --workers 3 "$@" 2>&1 | tee "$LOG_FILE"
