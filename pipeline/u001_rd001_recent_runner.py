"""Shared status helpers for the dedicated recent-window RD-001 runner."""

import os
import sys
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings


def status_path():
    configured = getattr(settings, 'U001_RD001_RECENT_RUNNER_STATUS_FILE', None)
    if configured:
        return Path(configured)
    if 'test' in sys.argv:
        return Path(settings.BASE_DIR) / 'logs' / 'u001_rd001_recent_runner_status.test.txt'
    return Path(settings.BASE_DIR) / 'logs' / 'u001_rd001_recent_runner_status.txt'


def read_status_file(path=None):
    path = path or status_path()
    try:
        content = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return {'_error': str(exc)}

    data = {}
    for raw_line in content.splitlines():
        if '=' not in raw_line:
            continue
        key, value = raw_line.split('=', 1)
        data[key.strip()] = value.strip()
    return data


def parse_runner_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def last_successful_cycle_at(raw):
    if not raw or raw.get('_error'):
        return None
    if str(raw.get('last_exit_code') or '') != '0':
        return None
    return parse_runner_datetime(raw.get('last_cycle_completed_at'))


def pid_alive(value):
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
