"""Conformance: Dexscreener pairs -> canonical PoolMapping dicts.

Pure function — no side effects, no DB writes, no API calls.
"""

from datetime import datetime, timezone


def conform(raw_pairs):
    """Transform Dexscreener pair objects to PoolMapping-compatible dicts.

    Args:
        raw_pairs: List of pair dicts from the Dexscreener /tokens/v1/ response.

    Returns:
        List of dicts with keys: coin_id, pool_address, dex, source, created_at.
        Only pairs where dexId == "pumpswap" are included.
    """
    results = []
    for pair in raw_pairs:
        if pair['dexId'] != 'pumpswap':
            continue
        results.append({
            'coin_id': pair['baseToken']['address'],
            'pool_address': pair['pairAddress'],
            'dex': pair['dexId'],
            'source': 'dexscreener',
            'created_at': datetime.fromtimestamp(
                pair['pairCreatedAt'] / 1000, tz=timezone.utc,
            ),
        })
    return results
