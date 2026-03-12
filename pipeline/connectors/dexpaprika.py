"""DexPaprika source connector for FL-001 OHLCV data."""

import logging
import time

logger = logging.getLogger(__name__)

from warehouse.models import OHLCVCandle

from pipeline.connectors.http import request_with_retry

BASE_URL = "https://api.dexpaprika.com"
MAX_PER_PAGE = 366
# Source-specific API parameter — maps to OHLCVCandle.TEMPORAL_RESOLUTION
INTERVAL = "5m"


def fetch_ohlcv(pool_address, start, end):
    """Fetch 5-min OHLCV candles from DexPaprika for a pool.

    Args:
        pool_address: Pumpswap pool address.
        start: datetime (UTC) — start of time range.
        end: datetime (UTC) — end of time range.

    Returns:
        Tuple of (records, metadata) where records is a list of raw JSON
        dicts and metadata is a dict with 'api_calls'.
    """
    all_records = []
    current_start = start
    api_calls = 0

    while current_start < end:
        url = (
            f"{BASE_URL}/networks/solana/pools/{pool_address}/ohlcv"
        )
        params = {
            'start': current_start.isoformat(),
            'end': end.isoformat(),
            'interval': INTERVAL,
            'limit': MAX_PER_PAGE,
        }

        data = request_with_retry(url, params)
        api_calls += 1

        if not data:
            break

        all_records.extend(data)

        # Paginate: next page starts from last record's timestamp
        last_time = data[-1].get('time_open', '')
        if last_time:
            from datetime import datetime, timezone
            last_dt = datetime.fromisoformat(
                last_time.replace('Z', '+00:00')
            )
            # Move past the last record to avoid duplicates
            current_start = last_dt + OHLCVCandle.TEMPORAL_RESOLUTION
        else:
            break

        if len(data) < MAX_PER_PAGE:
            break

        time.sleep(0.5)

    logger.info(
        "Fetched %d OHLCV candles for pool %s (%d API calls)",
        len(all_records), pool_address, api_calls,
    )
    meta = {'api_calls': api_calls}
    return all_records, meta


def fetch_token_pools(mint_address):
    """Fetch pool list for a token from DexPaprika.

    The /networks/solana/tokens/{mint}/pools endpoint returns
    {'pools': [...], 'page_info': {...}}, not a bare list.

    Returns:
        List of pool detail dicts.
    """
    url = f"{BASE_URL}/networks/solana/tokens/{mint_address}/pools"
    data = request_with_retry(url, params={})
    return data['pools']
