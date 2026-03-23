"""Binance Spot API connector for steady-state kline updates.

Uses data-api.binance.vision (not api.binance.com — may be geo-restricted).
No API key required. Public market data only.
"""

import logging
from datetime import datetime, timezone

from pipeline.connectors.http import request_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://data-api.binance.vision"
MAX_KLINES_PER_CALL = 1000


def fetch_klines_api(symbol, start, end, interval='1m'):
    """Fetch klines from Binance spot API with pagination.

    Args:
        symbol: e.g. 'BTCUSDT'
        start: datetime (UTC) — inclusive start
        end: datetime (UTC) — inclusive end
        interval: candle interval (default '1m')

    Returns:
        Tuple of (rows, metadata).
        rows: list of dicts with same keys as CSV connector output.
    """
    all_rows = []
    api_calls = 0
    current_start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    while current_start_ms < end_ms:
        url = f"{BASE_URL}/api/v3/klines"
        params = {
            'symbol': symbol,
            'interval': interval,
            'startTime': current_start_ms,
            'endTime': end_ms,
            'limit': MAX_KLINES_PER_CALL,
        }

        response = request_with_retry(url, params=params, max_retries=3)
        response.raise_for_status()
        api_calls += 1

        data = response.json()
        if not data:
            break

        for kline in data:
            all_rows.append({
                'timestamp': datetime.fromtimestamp(
                    kline[0] / 1000, tz=timezone.utc,
                ),
                'open': kline[1],
                'high': kline[2],
                'low': kline[3],
                'close': kline[4],
                'volume': kline[5],
                'close_time': kline[6],
                'quote_volume': kline[7],
                'trade_count': int(kline[8]),
                'taker_buy_volume': kline[9],
                'taker_buy_quote_volume': kline[10],
            })

        # Advance past the last kline's open time
        last_open_ms = data[-1][0]
        if last_open_ms == current_start_ms:
            # No progress — avoid infinite loop
            break
        current_start_ms = last_open_ms + 60_000  # next minute

    logger.info(
        "Fetched %d klines for %s via API (%d API calls)",
        len(all_rows), symbol, api_calls,
    )
    return all_rows, {'api_calls': api_calls, 'source': 'binance_api'}
