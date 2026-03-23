"""Conformance for U-002 FL-003: Binance futures metrics → U002FuturesMetrics dicts.

Pure function — no DB access, no API calls. Crashes on malformed input.
Input from binance_csv.fetch_futures_metrics_csv (DictReader rows).
"""

from decimal import Decimal


def conform(raw_rows, symbol, pool_address=None, **kwargs):
    """Transform raw futures metrics rows into canonical U002FuturesMetrics dicts.

    Args:
        raw_rows: List of dicts from binance_csv connector.
            Each has CSV header keys plus 'timestamp' (already parsed).
        symbol: Asset symbol (e.g. 'BTCUSDT').

    Returns:
        List of dicts matching U002FuturesMetrics fields.
    """
    canonical = []
    for row in raw_rows:
        canonical.append({
            'asset_id': symbol,
            'timestamp': row['timestamp'],
            'sum_open_interest': Decimal(str(row['sum_open_interest'])),
            'sum_open_interest_value': Decimal(str(row['sum_open_interest_value'])),
            'count_toptrader_long_short_ratio': Decimal(str(row['count_toptrader_long_short_ratio'])),
            'sum_toptrader_long_short_ratio': Decimal(str(row['sum_toptrader_long_short_ratio'])),
            'count_long_short_ratio': Decimal(str(row['count_long_short_ratio'])),
            'sum_taker_long_short_vol_ratio': Decimal(str(row['sum_taker_long_short_vol_ratio'])),
        })
    return canonical
