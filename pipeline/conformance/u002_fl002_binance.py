"""Conformance for U-002 FL-002: Binance order book → U002OrderBookSnapshot dicts.

Pure function — no DB access, no API calls. Crashes on malformed input.
Normalizes the order book into one row per level per side.
"""

from decimal import Decimal


def conform(raw_data, symbol, pool_address=None, **kwargs):
    """Transform raw order book snapshot into canonical rows.

    Args:
        raw_data: dict from Binance /api/v3/depth with 'bids', 'asks', 'lastUpdateId'.
        symbol: Asset symbol (e.g. 'BTCUSDT').
        **kwargs: Must contain 'capture_time' (datetime when snapshot was taken).

    Returns:
        List of dicts matching U002OrderBookSnapshot fields.
        40 rows for a 20-level book (20 bids + 20 asks).
    """
    capture_time = kwargs['capture_time']
    last_update_id = raw_data['lastUpdateId']

    canonical = []

    for level_idx, (price_str, qty_str) in enumerate(raw_data['bids'], start=1):
        canonical.append({
            'asset_id': symbol,
            'timestamp': capture_time,
            'side': 'bid',
            'level': level_idx,
            'price': Decimal(price_str),
            'quantity': Decimal(qty_str),
            'last_update_id': last_update_id,
        })

    for level_idx, (price_str, qty_str) in enumerate(raw_data['asks'], start=1):
        canonical.append({
            'asset_id': symbol,
            'timestamp': capture_time,
            'side': 'ask',
            'level': level_idx,
            'price': Decimal(price_str),
            'quantity': Decimal(qty_str),
            'last_update_id': last_update_id,
        })

    return canonical
