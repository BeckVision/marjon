#!/usr/bin/env bash

project_dir() {
    cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

preferred_venv_dir() {
    local root
    root="$(project_dir)"

    if [[ -d "$root/.venv" ]]; then
        printf '%s\n' "$root/.venv"
        return
    fi

    if [[ -d "$root/venv" ]]; then
        printf '%s\n' "$root/venv"
        return
    fi

    printf '%s\n' "$root/.venv"
}

python_bin() {
    local venv_dir
    venv_dir="$(preferred_venv_dir)"
    printf '%s\n' "$venv_dir/bin/python"
}

pip_bin() {
    local venv_dir
    venv_dir="$(preferred_venv_dir)"
    printf '%s\n' "$venv_dir/bin/pip"
}

ensure_venv() {
    local venv_dir py
    venv_dir="$(preferred_venv_dir)"

    if [[ -x "$venv_dir/bin/python" ]]; then
        return
    fi

    py="${PYTHON:-python3}"
    "$py" -m venv "$venv_dir"
}

ensure_env_file() {
    local root
    root="$(project_dir)"

    if [[ -f "$root/.env" ]]; then
        return
    fi

    cp "$root/.env.example" "$root/.env"
    printf 'Created %s/.env from .env.example\n' "$root"
}

wait_for_tcp() {
    local host="$1" port="$2" timeout_seconds="$3"
    local deadline=$((SECONDS + timeout_seconds))

    while (( SECONDS < deadline )); do
        if (echo >"/dev/tcp/$host/$port") >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done

    return 1
}
