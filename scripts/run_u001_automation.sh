#!/usr/bin/env bash
# Overlap-protected wrapper for one U-001 automation tick.
#
# Usage (cron):
#   */30 * * * * /home/beck/Desktop/projects/marjon/scripts/run_u001_automation.sh
#
# Usage (manual):
#   ./scripts/run_u001_automation.sh --dry-run

set -euo pipefail

LOCK_FILE="/tmp/marjon_u001_automation.lock"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/u001_automation_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date -Iseconds) U-001 automation tick already running, skipping." >&2
    exit 0
fi

cd "$PROJECT_DIR"
"$SCRIPT_DIR/manage.sh" automate_u001 "$@" 2>&1 | tee "$LOG_FILE"
