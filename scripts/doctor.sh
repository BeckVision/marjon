#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/_common.sh"

PROJECT_DIR="$(project_dir)"
VENV_DIR="$(preferred_venv_dir)"
PYTHON_BIN="$(python_bin)"

pass() {
    printf '[ok] %s\n' "$1"
}

warn() {
    printf '[warn] %s\n' "$1"
}

fail() {
    printf '[fail] %s\n' "$1" >&2
    exit 1
}

cd "$PROJECT_DIR"

printf 'Project: %s\n' "$PROJECT_DIR"
printf 'Python:  %s\n' "$PYTHON_BIN"

[[ -f .env ]] || fail ".env is missing. Run ./scripts/bootstrap.sh first."
pass ".env present"

if [[ -d .venv && -d venv ]]; then
    warn "Both .venv and venv exist. Scripts will prefer $VENV_DIR."
fi

[[ -x "$PYTHON_BIN" ]] || fail "Python executable not found in $VENV_DIR."
pass "virtualenv present at $VENV_DIR"

"$PYTHON_BIN" -c "import django, httpx, psycopg2, dotenv" >/dev/null \
    || fail "Required Python packages are missing from $VENV_DIR."
pass "required Python packages import correctly"

if "$PYTHON_BIN" -c "import socket; s = socket.create_connection(('127.0.0.1', 5433), timeout=2); s.close()" >/dev/null 2>&1; then
    pass "PostgreSQL is reachable on localhost:5433"
else
    fail "PostgreSQL is not reachable on localhost:5433. Start it with docker compose up -d db."
fi

"$SCRIPT_DIR/manage.sh" check >/dev/null
pass "Django system checks pass"

"$SCRIPT_DIR/manage.sh" migrate --check >/dev/null
pass "no unapplied migrations"

"$SCRIPT_DIR/manage.sh" shell -c "from django.db import connection; connection.ensure_connection(); print('db-ok')" >/dev/null \
    || fail "Django cannot connect to the configured database."
pass "Django database connection succeeds"

printf 'Doctor finished successfully.\n'
