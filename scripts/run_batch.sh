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
MAX_COINS="${MARJON_U001_RD001_MAX_COINS:-25}"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "$(date -Iseconds) Batch already running, skipping." >&2
    exit 0
fi

cd "$PROJECT_DIR"
"$SCRIPT_DIR/manage.sh" fetch_transactions_batch \
    --workers "${MARJON_U001_RD001_BATCH_WORKERS:-1}" \
    --parse-workers "${MARJON_U001_RD001_PARSE_WORKERS:-1}" \
    --rpc-batch-size "${MARJON_U001_RD001_RPC_BATCH_SIZE:-100}" \
    --max-coins "$MAX_COINS" \
    --max-new-sigs "${MARJON_U001_RD001_MAX_NEW_SIGS:-500}" \
    --max-bootstrap-new-sigs "${MARJON_U001_RD001_MAX_BOOTSTRAP_NEW_SIGS:-400}" \
    --min-sigs "${MARJON_U001_RD001_MIN_SIGS:-3}" \
    --min-steady-state-sigs "${MARJON_U001_RD001_MIN_STEADY_STATE_SIGS:-1}" \
    --sleep "${MARJON_U001_RD001_SLEEP:-1.0}" \
    "$@"
