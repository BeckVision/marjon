"""Binance order book depth connector for U-002 FL-002.

Polls current order book state via data-api.binance.vision.
Returns current snapshot only — no historical data available.
"""

import logging
from datetime import datetime, timezone

from pipeline.connectors.http import request_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://data-api.binance.vision"


def fetch_order_book(symbol, depth=20):
    """Fetch current order book snapshot for a symbol.

    Args:
        symbol: e.g. 'BTCUSDT'
        depth: Number of levels per side (default 20).

    Returns:
        Tuple of (raw_data, metadata).
        raw_data: dict with 'bids', 'asks', 'lastUpdateId'.
        metadata: dict with api_calls, capture_time.
    """
    url = f"{BASE_URL}/api/v3/depth"
    params = {'symbol': symbol, 'limit': depth}

    data = request_with_retry(url, params=params, max_retries=3)
    capture_time = datetime.now(timezone.utc)

    logger.debug(
        "Fetched order book for %s: %d bids, %d asks, updateId=%s",
        symbol, len(data.get('bids', [])), len(data.get('asks', [])),
        data.get('lastUpdateId'),
    )

    return data, {
        'api_calls': 1,
        'capture_time': capture_time,
        'source': 'binance_api',
    }
