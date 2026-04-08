#!/usr/bin/env bash
# Explicit opt-in wrapper for RD-001 partial rows parked by the free-tier guard.
# Uses Helius against the historical backlog and keeps the slice very small.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAX_COINS="${MARJON_U001_RD001_PARTIAL_GUARDED_MAX_COINS:-1}"

"$SCRIPT_DIR/run_batch.sh" \
    --status-filter partial \
    --source helius \
    --include-free-tier-guarded \
    --only-free-tier-guarded \
    --max-coins "$MAX_COINS" \
    "$@"
