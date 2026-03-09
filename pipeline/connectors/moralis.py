"""Moralis source connector for FL-002 holder snapshot data."""

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://solana-gateway.moralis.io"
CU_PER_CALL = 50
DAILY_CU_LIMIT = 40000
MAX_PER_PAGE = 100


def fetch_holders(mint_address, start, end):
    """Fetch 5-min holder snapshots from Moralis for a token.

    Args:
        mint_address: Token mint address (direct query key).
        start: datetime (UTC) — start of time range.
        end: datetime (UTC) — end of time range.

    Returns:
        List of raw JSON dicts in ascending order (reversed from API).
    """
    api_key = os.environ.get('MORALIS_API_KEY')
    if not api_key:
        raise RuntimeError("MORALIS_API_KEY environment variable not set")

    all_records = []
    cursor = None
    cu_used = 0

    url = (
        f"{BASE_URL}/token/mainnet/holders/"
        f"{mint_address}/historical"
    )

    while True:
        params = {
            'timeFrame': '5min',
            'fromDate': start.isoformat(),
            'toDate': end.isoformat(),
            'limit': MAX_PER_PAGE,
        }
        if cursor:
            params['cursor'] = cursor

        headers = {'X-Api-Key': api_key}

        data = _request_with_retry(url, params, headers)
        cu_used += CU_PER_CALL

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

    logger.info(
        "Fetched %d holder snapshots for %s (CU used: %d)",
        len(all_records), mint_address, cu_used,
    )
    return all_records


def _request_with_retry(url, params, headers, max_retries=3):
    """Make GET request with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            resp = requests.get(
                url, params=params, headers=headers, timeout=30,
            )

            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "Rate limited (429), waiting %ds", wait
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "Server error %d, waiting %ds",
                    resp.status_code, wait,
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()

            # Moralis can return 200 with error body
            if isinstance(data, dict) and 'message' in data:
                if 'error' in data.get('message', '').lower():
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "Moralis error body: %s, waiting %ds",
                        data['message'], wait,
                    )
                    time.sleep(wait)
                    continue

            return data

        except requests.exceptions.Timeout:
            wait = 2 ** (attempt + 1)
            logger.warning("Timeout, waiting %ds", wait)
            time.sleep(wait)
            continue

    raise RuntimeError(
        f"Failed after {max_retries} retries: {url}"
    )
