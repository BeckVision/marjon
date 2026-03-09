"""Conformance function: DexPaprika raw JSON -> canonical OHLCVCandle dicts.

Pure function — no side effects, no DB writes, no API calls.
"""

from datetime import datetime, timezone
from decimal import Decimal


def conform(raw_response, mint_address):
    """Transform raw DexPaprika OHLCV response to canonical form.

    Args:
        raw_response: List of dicts from DexPaprika API.
        mint_address: String mint address for FK resolution.

    Returns:
        List of dicts matching OHLCVCandle field names.
    """
    now = datetime.now(timezone.utc)
    records = []

    for raw in raw_response:
        time_open = raw['time_open']
        if time_open.endswith('Z'):
            time_open = time_open[:-1] + '+00:00'
        ts = datetime.fromisoformat(time_open)

        records.append({
            'timestamp': ts,
            'open_price': Decimal(str(raw['open'])),
            'high_price': Decimal(str(raw['high'])),
            'low_price': Decimal(str(raw['low'])),
            'close_price': Decimal(str(raw['close'])),
            'volume': Decimal(str(raw['volume'])),
            'coin_id': mint_address,
            'ingested_at': now,
        })

    return records
