#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$(cd "$SCRIPT_DIR/.." && pwd)"
docker compose up -d db
exec "$SCRIPT_DIR/manage.sh" test --verbosity 1 --noinput "$@"
