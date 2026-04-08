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
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/daily_$(date +%Y%m%d_%H%M%S).log"
DAILY_STEPS="${MARJON_U001_DAILY_STEPS:-discovery,pool_mapping,ohlcv}"
DAILY_COINS="${MARJON_U001_DAILY_COINS:-50}"
DAILY_DAYS="${MARJON_U001_DAILY_DAYS:-20}"
DAILY_MATURE_ONLY="${MARJON_U001_DAILY_MATURE_ONLY:-1}"

if [[ "${MARJON_U001_ENABLE_HOLDERS:-0}" == "1" ]]; then
    DAILY_STEPS="${DAILY_STEPS},holders"
fi

mkdir -p "$LOG_DIR"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date -Iseconds) Daily run already in progress, skipping." >&2
    exit 0
fi

cd "$PROJECT_DIR"
CMD=(
    "$SCRIPT_DIR/manage.sh" orchestrate
    --universe u001
    --steps "$DAILY_STEPS"
    --coins "$DAILY_COINS"
    --days "$DAILY_DAYS"
)
if [[ "$DAILY_MATURE_ONLY" == "1" ]]; then
    CMD+=(--mature-only)
fi
"${CMD[@]}" "$@" 2>&1 | tee "$LOG_FILE"
