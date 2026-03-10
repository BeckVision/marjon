"""Moralis source connector for U-001 universe discovery (graduated tokens)."""

import logging
import os
import time

import requests

from pipeline.connectors.moralis import (
    BASE_URL,
    CU_PER_CALL,
    record_cu_used,
)

logger = logging.getLogger(__name__)


def fetch_graduated_tokens(cursor=None, limit=100):
    """Fetch one page of graduated pump.fun tokens from Moralis.

    Args:
        cursor: Pagination cursor from previous call, or None for first page.
        limit: Number of tokens per page (max 100).

    Returns:
        Dict with 'result' (list of token dicts), 'cursor' (str or None),
        'pageSize', 'page'.
    """
    api_key = os.environ.get('MORALIS_API_KEY')
    if not api_key:
        raise RuntimeError("MORALIS_API_KEY environment variable not set")

    url = f"{BASE_URL}/token/mainnet/exchange/pumpfun/graduated"
    params = {'limit': limit}
    if cursor:
        params['cursor'] = cursor

    headers = {'X-Api-Key': api_key}

    if cursor is None:
        logger.info("Fetching graduated tokens (first page, limit=%d)", limit)

    data = _request_with_retry(url, params, headers)

    result = data.get('result', [])
    next_cursor = data.get('cursor')

    record_cu_used(CU_PER_CALL)

    logger.info(
        "Fetched %d graduated tokens (page=%s, cursor=%s)",
        len(result),
        data.get('page'),
        'present' if next_cursor else 'null',
    )
    logger.debug("Page details: pageSize=%s", data.get('pageSize'))

    return data


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
            data = resp.json()

            # Moralis can return 200 with error body (no 'result' key)
            if isinstance(data, dict) and 'message' in data and 'result' not in data:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "Moralis error body: %s, waiting %ds (url=%s)",
                    data['message'], wait, url, exc_info=True,
                )
                time.sleep(wait)
                continue

            return data

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            wait = 2 ** (attempt + 1)
            logger.warning(
                "Network error, waiting %ds (url=%s)",
                wait, url, exc_info=True,
            )
            time.sleep(wait)
            continue

        except ValueError:
            # json.JSONDecodeError is a subclass of ValueError
            wait = 2 ** (attempt + 1)
            logger.warning(
                "JSON decode error, waiting %ds (url=%s)",
                wait, url, exc_info=True,
            )
            time.sleep(wait)
            continue

    raise RuntimeError(
        f"Failed after {max_retries} retries: {url}"
    )
