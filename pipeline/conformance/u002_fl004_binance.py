"""Conformance for U-002 FL-004: Binance funding rate → U002FundingRate dicts.

Pure function — no DB access, no API calls. Crashes on malformed input.
Input from binance_csv.fetch_funding_rate_csv (DictReader rows).
Note: CSV has no symbol column — symbol is passed from the connector.
"""

from decimal import Decimal


def conform(raw_rows, symbol, pool_address=None, **kwargs):
    """Transform raw funding rate rows into canonical U002FundingRate dicts.

    Args:
        raw_rows: List of dicts from binance_csv connector.
            Each has CSV header keys plus 'timestamp' (already parsed)
            and 'symbol' (injected by connector from filename).
        symbol: Asset symbol (e.g. 'BTCUSDT').

    Returns:
        List of dicts matching U002FundingRate fields.
    """
    canonical = []
    for row in raw_rows:
        canonical.append({
            'asset_id': symbol,
            'timestamp': row['timestamp'],
            'funding_interval_hours': int(row['funding_interval_hours']),
            'last_funding_rate': Decimal(str(row['last_funding_rate'])),
        })
    return canonical
