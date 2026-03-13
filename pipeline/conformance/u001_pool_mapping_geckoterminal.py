"""Conformance: GeckoTerminal batch pool response -> canonical PoolMapping dicts.

Pure function — no side effects, no DB writes, no API calls.
Handles JSON:API sideloading: pool details are in included[], referenced
by ID from token objects in data[].
"""

from datetime import datetime, timezone


def conform(raw_response):
    """Transform GeckoTerminal /tokens/multi/ response to PoolMapping-compatible dicts.

    Args:
        raw_response: Full JSON:API response dict with 'data' and 'included' keys.

    Returns:
        List of dicts with keys: coin_id, pool_address, dex, source, created_at.
        Only pools where dex.data.id == "pumpswap" are included.
    """
    included = raw_response.get('included', [])
    if not included:
        return []

    pool_lookup = {pool['id']: pool for pool in included}

    results = []
    for token in raw_response['data']:
        pool_refs = token['relationships']['top_pools']['data']
        for pool_ref in pool_refs:
            pool = pool_lookup[pool_ref['id']]
            dex_id = pool['relationships']['dex']['data']['id']
            if dex_id != 'pumpswap':
                continue

            created_str = pool['attributes']['pool_created_at']
            created_at = datetime.fromisoformat(
                created_str.replace("Z", "+00:00")
            )

            results.append({
                'coin_id': pool['relationships']['base_token']['data']['id'].replace('solana_', '', 1),
                'pool_address': pool['attributes']['address'],
                'dex': dex_id,
                'source': 'geckoterminal',
                'created_at': created_at,
            })
    return results
