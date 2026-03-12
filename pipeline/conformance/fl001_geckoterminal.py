"""Conformance function: GeckoTerminal raw arrays -> canonical OHLCVCandle dicts.

Pure function — no side effects, no DB writes, no API calls.
Strict: crashes if a candle array has fewer than 6 elements. GeckoTerminal's
schema guarantees [timestamp, open, high, low, close, volume] — a short
array means a broken upstream contract, which must surface as a loud failure.
"""

from datetime import datetime, timezone
from decimal import Decimal


def conform(raw_candles, mint_address):
    """Transform raw GeckoTerminal OHLCV arrays to canonical form.

    Args:
        raw_candles: List of [timestamp, open, high, low, close, volume] arrays.
            Timestamps are Unix epoch integers. Already sorted ascending by
            the connector.
        mint_address: String mint address for FK resolution.

    Returns:
        List of dicts matching OHLCVCandle field names.
        Note: ingested_at is handled by the model's auto_now_add=True.
    """
    records = []

    for candle in raw_candles:
        ts = datetime.fromtimestamp(candle[0], tz=timezone.utc)

        records.append({
            'timestamp': ts,
            'open_price': Decimal(str(candle[1])),
            'high_price': Decimal(str(candle[2])),
            'low_price': Decimal(str(candle[3])),
            'close_price': Decimal(str(candle[4])),
            'volume': Decimal(str(candle[5])),
            'coin_id': mint_address,
        })

    return records
