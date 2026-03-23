"""Conformance for U-002 FL-001: Binance spot klines → U002OHLCVCandle dicts.

Pure function — no DB access, no API calls. Crashes on malformed input.
Works for both CSV and API connector output (same dict structure).
"""

from decimal import Decimal


def conform(raw_rows, symbol, pool_address=None, **kwargs):
    """Transform raw kline rows into canonical U002OHLCVCandle dicts.

    Args:
        raw_rows: List of dicts from binance_csv or binance_api connector.
            Each has: timestamp, open, high, low, close, volume,
            quote_volume, trade_count, taker_buy_volume, taker_buy_quote_volume.
        symbol: Asset symbol (e.g. 'BTCUSDT').

    Returns:
        List of dicts matching U002OHLCVCandle fields.
    """
    canonical = []
    for row in raw_rows:
        canonical.append({
            'asset_id': symbol,
            'timestamp': row['timestamp'],
            'open_price': Decimal(str(row['open'])),
            'high_price': Decimal(str(row['high'])),
            'low_price': Decimal(str(row['low'])),
            'close_price': Decimal(str(row['close'])),
            'volume': Decimal(str(row['volume'])),
            'quote_volume': Decimal(str(row['quote_volume'])),
            'trade_count': int(row['trade_count']),
            'taker_buy_volume': Decimal(str(row['taker_buy_volume'])),
            'taker_buy_quote_volume': Decimal(str(row['taker_buy_quote_volume'])),
        })
    return canonical
