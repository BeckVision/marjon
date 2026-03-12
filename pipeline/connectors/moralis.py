"""Moralis source connector for FL-002 holder snapshot data."""

import json
import logging
import math
import os
import time
from datetime import date
from pathlib import Path

from pipeline.connectors.http import request_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://solana-gateway.moralis.io"
CU_PER_CALL = 50
DAILY_CU_LIMIT = 40000
MAX_PER_PAGE = 100

# Daily CU tracker file (project root)
_CU_TRACKER_PATH = Path(__file__).resolve().parent.parent.parent / '.moralis_cu_tracker.json'


def _validate_moralis_response(data):
    """Raise if Moralis returned a 200 with an error body."""
    if isinstance(data, dict) and 'message' in data and 'result' not in data:
        raise ValueError(f"Moralis error body: {data['message']}")


def get_daily_cu_used():
    """Read today's CU usage from the tracker file."""
    try:
        data = json.loads(_CU_TRACKER_PATH.read_text())
        if data.get('date') == str(date.today()):
            return data.get('cu_used', 0)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return 0


def record_cu_used(cu):
    """Add CU to today's daily tracker."""
    today = str(date.today())
    current = 0
    try:
        data = json.loads(_CU_TRACKER_PATH.read_text())
        if data.get('date') == today:
            current = data.get('cu_used', 0)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    _CU_TRACKER_PATH.write_text(json.dumps({
        'date': today,
        'cu_used': current + cu,
    }))


def estimate_cu_cost(start, end):
    """Estimate CU cost for a holder snapshot fetch."""
    intervals = max(1, (end - start).total_seconds() / 300)
    pages = math.ceil(intervals / MAX_PER_PAGE)
    return pages * CU_PER_CALL


def fetch_holders(mint_address, start, end):
    """Fetch 5-min holder snapshots from Moralis for a token.

    Args:
        mint_address: Token mint address (direct query key).
        start: datetime (UTC) — start of time range.
        end: datetime (UTC) — end of time range.

    Returns:
        Tuple of (records, metadata) where records is a list of raw JSON
        dicts in ascending order (reversed from API) and metadata is a
        dict with 'api_calls' and 'cu_consumed'.
    """
    api_key = os.environ.get('MORALIS_API_KEY')
    if not api_key:
        raise RuntimeError("MORALIS_API_KEY environment variable not set")

    all_records = []
    cursor = None
    cu_used = 0
    api_calls = 0

    url = (
        f"{BASE_URL}/token/mainnet/holders/"
        f"{mint_address}/historical"
    )

    while True:
        params = {
            # Source-specific API parameter — maps to HolderSnapshot.TEMPORAL_RESOLUTION
            'timeFrame': '5min',
            'fromDate': start.isoformat(),
            'toDate': end.isoformat(),
            'limit': MAX_PER_PAGE,
        }
        if cursor:
            params['cursor'] = cursor

        headers = {'X-Api-Key': api_key}

        data = request_with_retry(
            url, params, headers=headers,
            validate_response=_validate_moralis_response,
        )
        cu_used += CU_PER_CALL
        api_calls += 1

        if not data:
            break

        result = data.get('result', [])
        if not result:
            break

        all_records.extend(result)

        cursor = data.get('cursor')
        if not cursor:
            break

        time.sleep(0.5)

    # Moralis returns descending (newest first) — reverse to ascending
    all_records.reverse()

    record_cu_used(cu_used)

    logger.info(
        "Fetched %d holder snapshots for %s (CU used: %d, API calls: %d)",
        len(all_records), mint_address, cu_used, api_calls,
    )
    meta = {'api_calls': api_calls, 'cu_consumed': cu_used}
    return all_records, meta
