"""Conformance for U-002 FL-003: Binance futures metrics → U002FuturesMetrics dicts.

Pure function — no DB access, no API calls. Crashes on malformed input.
Input from binance_csv.fetch_futures_metrics_csv (DictReader rows).

Note: ratio fields can be empty strings in Binance CSVs (documented as
nullable in the data spec). Empty strings → None.
"""

from decimal import Decimal


def _to_decimal_or_none(value):
    """Convert to Decimal, or None if empty/missing."""
    s = str(value).strip()
    if not s:
        return None
    return Decimal(s)


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
            'sum_open_interest': _to_decimal_or_none(row['sum_open_interest']),
            'sum_open_interest_value': _to_decimal_or_none(row['sum_open_interest_value']),
            'count_toptrader_long_short_ratio': _to_decimal_or_none(row['count_toptrader_long_short_ratio']),
            'sum_toptrader_long_short_ratio': _to_decimal_or_none(row['sum_toptrader_long_short_ratio']),
            'count_long_short_ratio': _to_decimal_or_none(row['count_long_short_ratio']),
            'sum_taker_long_short_vol_ratio': _to_decimal_or_none(row['sum_taker_long_short_vol_ratio']),
        })
    return canonical
