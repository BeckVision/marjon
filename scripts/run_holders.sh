#!/usr/bin/env bash
# Overlap-protected wrapper for U-001 FL-002 holders catch-up.
# Uses the existing orchestrator with a holders-only work list.
#
# Usage (cron):
#   0 */6 * * *  /home/beck/Desktop/projects/marjon/scripts/run_holders.sh
#
# Usage (manual):
#   ./scripts/run_holders.sh --dry-run

set -euo pipefail

LOCK_FILE="/tmp/marjon_holders.lock"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/holders_$(date +%Y%m%d_%H%M%S).log"
HOLDERS_COINS="${MARJON_U001_HOLDERS_COINS:-10}"
HOLDERS_DAYS="${MARJON_U001_HOLDERS_DAYS:-20}"
HOLDERS_MATURE_ONLY="${MARJON_U001_HOLDERS_MATURE_ONLY:-1}"

mkdir -p "$LOG_DIR"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date -Iseconds) Holders run already in progress, skipping." >&2
    exit 0
fi

cd "$PROJECT_DIR"
CMD=(
    "$SCRIPT_DIR/manage.sh" orchestrate
    --universe u001
    --steps holders
    --coins "$HOLDERS_COINS"
    --days "$HOLDERS_DAYS"
)
if [[ "$HOLDERS_MATURE_ONLY" == "1" ]]; then
    CMD+=(--mature-only)
fi
"${CMD[@]}" "$@" 2>&1 | tee "$LOG_FILE"
