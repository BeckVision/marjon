"""Shared utilities for conformance functions."""

from datetime import datetime


def parse_iso_timestamp(ts_string):
    """Parse ISO 8601 timestamp string to UTC-aware datetime.

    Handles the trailing 'Z' suffix that fromisoformat doesn't support
    in Python < 3.11.
    """
    cleaned = ts_string.replace('Z', '+00:00')
    return datetime.fromisoformat(cleaned)


def make_skipped(tx_signature, timestamp, mint_address, pool_address,
                 tx, reason, tx_type=None, tx_status=None):
    """Build a SkippedTransaction dict.

    Shared by Shyft and Helius conformance functions.

    Args:
        tx_signature: Transaction signature string.
        timestamp: UTC-aware datetime.
        mint_address: Coin FK.
        pool_address: Pool address.
        tx: Raw transaction dict (stored in raw_json).
        reason: SkipReason value.
        tx_type: Override for tx type. Defaults to tx.get('type', 'UNKNOWN').
        tx_status: Override for tx status. Defaults to tx.get('status', 'UNKNOWN').
    """
    return {
        'tx_signature': tx_signature,
        'timestamp': timestamp,
        'coin_id': mint_address,
        'pool_address': pool_address,
        'tx_type': tx_type or tx.get('type', 'UNKNOWN'),
        'tx_status': tx_status or tx.get('status', 'UNKNOWN'),
        'skip_reason': reason,
        'raw_json': tx,
    }
