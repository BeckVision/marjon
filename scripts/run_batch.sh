#!/usr/bin/env bash
# Overlap-protected wrapper for fetch_transactions_batch.
# Uses flock to ensure only one instance runs at a time.
# If a previous run is still going, this exits immediately.
#
# Usage (cron):
#   0 * * * *  /home/beck/Desktop/projects/marjon/scripts/run_batch.sh
#
# Usage (manual, pass-through args):
#   ./scripts/run_batch.sh --source helius --max-coins 50

set -euo pipefail

LOCK_FILE="/tmp/marjon_batch.lock"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date -Iseconds) Batch already running, skipping." >&2
    exit 0
fi

cd "$PROJECT_DIR"
"$SCRIPT_DIR/manage.sh" fetch_transactions_batch "$@"
