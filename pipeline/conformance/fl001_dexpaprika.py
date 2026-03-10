"""Conformance function: DexPaprika raw JSON -> canonical OHLCVCandle dicts.

Pure function — no side effects, no DB writes, no API calls.
Strict: crashes on missing/None fields. DexPaprika's contract guarantees
non-nullable OHLCV fields — a None here means a broken upstream contract,
which must surface as a loud failure, not a silent default.
"""

from datetime import datetime
from decimal import Decimal


def conform(raw_response, mint_address):
    """Transform raw DexPaprika OHLCV response to canonical form.

    Args:
        raw_response: List of dicts from DexPaprika API.
        mint_address: String mint address for FK resolution.

    Returns:
        List of dicts matching OHLCVCandle field names.
        Note: ingested_at is handled by the model's auto_now_add=True.
    """
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
        })

    return records
