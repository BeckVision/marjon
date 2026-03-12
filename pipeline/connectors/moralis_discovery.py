"""Moralis source connector for U-001 universe discovery (graduated tokens)."""

import logging
import os

from pipeline.connectors.http import request_with_retry
from pipeline.connectors.moralis import (
    BASE_URL,
    CU_PER_CALL,
    _validate_moralis_response,
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

    data = request_with_retry(
        url, params, headers=headers,
        validate_response=_validate_moralis_response,
    )

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
