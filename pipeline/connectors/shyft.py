"""Shyft source connector for RD-001 raw transaction data."""

import itertools
import logging
import threading
import time

from django.conf import settings

from pipeline.conformance.utils import parse_iso_timestamp
from pipeline.connectors.http import request_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.shyft.to/sol/v1"
MAX_PER_PAGE = 100
RATE_LIMIT_SLEEP = 1.0  # seconds between calls per key

# Round-robin iterator over Shyft API keys (thread-safe)
_key_pool = itertools.cycle(settings.SHYFT_API_KEYS)
_key_lock = threading.Lock()


def _next_api_key():
    """Return the next API key from the rotation (thread-safe)."""
    with _key_lock:
        return next(_key_pool)


def _validate_shyft_response(data):
    """Raise if Shyft returned 200 with success=false."""
    if isinstance(data, dict) and data.get('success') is False:
        msg = data.get('message', 'Unknown Shyft error')
        raise ValueError(f"Shyft error: {msg}")


def fetch_transactions(pool_address, start=None, end=None):
    """Fetch transaction history for a Pumpswap pool from Shyft.

    Paginates backward from newest. If start is provided, stops when
    oldest transaction is before start (client-side time filtering).

    Args:
        pool_address: Pumpswap pool address string.
        start: Optional datetime (UTC) — stop fetching when tx.timestamp < start.
        end: Optional datetime (UTC) — currently unused (no server-side filtering).

    Returns:
        Tuple of (transactions, metadata) where transactions is a list of
        raw Shyft transaction dicts and metadata is {'api_calls': int}.
    """
    all_transactions = []
    api_calls = 0
    cursor = None  # before_tx_signature for pagination

    while True:
        params = {
            'network': 'mainnet-beta',
            'account': pool_address,
            'tx_num': str(MAX_PER_PAGE),
            'enable_events': 'true',
            'enable_raw': 'false',
        }
        if cursor:
            params['before_tx_signature'] = cursor

        api_key = _next_api_key()
        headers = {'x-api-key': api_key}

        url = f"{BASE_URL}/transaction/history"
        data = request_with_retry(
            url, params, headers=headers,
            validate_response=_validate_shyft_response,
        )
        api_calls += 1

        result = data.get('result', [])
        if not result:
            break

        all_transactions.extend(result)

        # Client-side time filtering: stop when oldest tx is before start
        if start is not None:
            oldest_tx = result[-1]
            oldest_ts = parse_iso_timestamp(oldest_tx['timestamp'])
            if oldest_ts < start:
                break

        # Last page: fewer results than requested
        if len(result) < MAX_PER_PAGE:
            break

        # Set cursor for next page: last transaction's first signature
        cursor = result[-1]['signatures'][0]

        time.sleep(RATE_LIMIT_SLEEP)

    logger.info(
        "Fetched %d transactions for pool %s (%d API calls)",
        len(all_transactions), pool_address, api_calls,
    )
    meta = {'api_calls': api_calls}
    return all_transactions, meta
