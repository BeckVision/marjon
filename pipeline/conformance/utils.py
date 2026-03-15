"""Shared utilities for conformance functions."""

from datetime import datetime


def parse_iso_timestamp(ts_string):
    """Parse ISO 8601 timestamp string to UTC-aware datetime.

    Handles the trailing 'Z' suffix that fromisoformat doesn't support
    in Python < 3.11.
    """
    cleaned = ts_string.replace('Z', '+00:00')
    return datetime.fromisoformat(cleaned)
