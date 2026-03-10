"""DexPaprika source connector for FL-001 OHLCV data."""

import logging
import time

import requests

logger = logging.getLogger(__name__)

from warehouse.models import OHLCVCandle

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
            'inversed': 'true',
        }

        data = _request_with_retry(url, params)
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
    data = _request_with_retry(url, params={})
    return data['pools']


def _request_with_retry(url, params, max_retries=3):
    """Make GET request with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=30)

            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "Rate limited (429), waiting %ds (url=%s)",
                    wait, url, exc_info=True,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "Server error %d, waiting %ds (url=%s)",
                    resp.status_code, wait, url, exc_info=True,
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            wait = 2 ** (attempt + 1)
            logger.warning(
                "Network error, waiting %ds (url=%s)",
                wait, url, exc_info=True,
            )
            time.sleep(wait)
            continue

    raise RuntimeError(
        f"Failed after {max_retries} retries: {url}"
    )
