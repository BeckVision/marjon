"""Dexscreener source connector for pool discovery."""

import logging

from pipeline.connectors.http import request_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"
BATCH_SIZE = 30


def fetch_token_pools_batch(mint_addresses):
    """Fetch pool pairs from Dexscreener for a batch of token addresses.

    Args:
        mint_addresses: List of token mint address strings (max 30).

    Returns:
        Tuple of (records, metadata) where records is the raw JSON
        response (list of pair dicts) and metadata is a dict with
        'api_calls'.
    """
    if len(mint_addresses) > BATCH_SIZE:
        raise ValueError(
            f"Batch size {len(mint_addresses)} exceeds maximum of {BATCH_SIZE}"
        )

    addresses = ",".join(mint_addresses)
    url = f"{BASE_URL}/tokens/v1/solana/{addresses}"
    pair_list = request_with_retry(url, params={})

    return pair_list, {'api_calls': 1}
