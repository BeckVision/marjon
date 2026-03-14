"""GeckoTerminal source connector for FL-001 OHLCV data."""

import itertools
import logging
import threading
import time

from django.conf import settings

from pipeline.connectors.http import request_with_retry

logger = logging.getLogger(__name__)

DIRECT_URL = "https://api.geckoterminal.com"
MAX_PER_PAGE = 1000
HEADERS = {'Accept': 'application/json'}

# Round-robin iterator over gateway URLs, falls back to direct if none configured
_gateway_pool = itertools.cycle(
    settings.GATEWAY_URLS if settings.GATEWAY_URLS else [DIRECT_URL]
)
_gateway_lock = threading.Lock()


def _next_base_url():
    """Return the next base URL from the gateway rotation (thread-safe)."""
    with _gateway_lock:
        return next(_gateway_pool)


def fetch_ohlcv(pool_address, start, end):
    """Fetch 5-min OHLCV candles from GeckoTerminal for a pool.

    Paginates backward using before_timestamp until the start of the
    requested range is covered.

    Args:
        pool_address: Pumpswap pool address.
        start: datetime (UTC) — start of time range.
        end: datetime (UTC) — end of time range.

    Returns:
        Tuple of (records, metadata) where records is a list of raw candle
        arrays [timestamp, o, h, l, c, v] in **ascending** order (reversed
        from API) and metadata is a dict with 'api_calls'.
    """
    path = f"/api/v2/networks/solana/pools/{pool_address}/ohlcv/minute"
    start_ts = int(start.timestamp())
    before_ts = int(end.timestamp())
    all_candles = []
    api_calls = 0

    while True:
        params = {
            'aggregate': '5',
            'before_timestamp': str(before_ts),
            'limit': MAX_PER_PAGE,
            'currency': 'usd',
        }

        base_url = _next_base_url()
        url = f"{base_url}{path}"
        data = request_with_retry(url, params, headers=HEADERS)
        api_calls += 1

        try:
            page = data['data']['attributes']['ohlcv_list']
        except (KeyError, TypeError):
            break
        if not page:
            break

        all_candles.extend(page)

        # GT returns descending — last element is oldest on this page
        oldest_ts = min(row[0] for row in page)
        if oldest_ts <= start_ts:
            break  # Covered the full range
        if len(page) < MAX_PER_PAGE:
            break  # No more data

        before_ts = oldest_ts

    # Filter to requested window
    all_candles = [
        c for c in all_candles
        if start_ts <= c[0] <= int(end.timestamp())
    ]

    # Deduplicate by timestamp (overlap from pagination boundaries)
    seen = set()
    deduped = []
    for c in all_candles:
        if c[0] not in seen:
            seen.add(c[0])
            deduped.append(c)

    # Reverse to ascending order (oldest first)
    deduped.sort(key=lambda c: c[0])

    logger.info(
        "Fetched %d OHLCV candles for pool %s (%d API calls)",
        len(deduped), pool_address, api_calls,
    )
    meta = {'api_calls': api_calls}
    return deduped, meta


def fetch_token_pools_batch(mint_addresses):
    """Fetch pool info from GeckoTerminal for a batch of token addresses.

    Uses the /tokens/multi/ endpoint with include=top_pools to get
    sideloaded pool data in a single call.

    Args:
        mint_addresses: List of token mint address strings (max 30).

    Returns:
        Tuple of (response_dict, metadata) where response_dict is the
        full JSON:API response (with 'data' and 'included' keys) and
        metadata is a dict with 'api_calls'.
    """
    if len(mint_addresses) > 30:
        raise ValueError(
            f"Batch size {len(mint_addresses)} exceeds maximum of 30"
        )

    addresses = ",".join(mint_addresses)
    base_url = _next_base_url()
    url = f"{base_url}/api/v2/networks/solana/tokens/multi/{addresses}"
    response_dict = request_with_retry(
        url, params={'include': 'top_pools'}, headers=HEADERS,
    )

    return response_dict, {'api_calls': 1}
