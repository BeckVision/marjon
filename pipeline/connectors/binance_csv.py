"""Binance CSV download connector for bulk historical data.

Downloads zip files from data.binance.vision, extracts CSV, parses rows.
Handles spot klines (no header, ms/μs timestamps), futures metrics (header,
datetime strings), and funding rate (header, ms timestamps).

No API key required. No rate limits documented, but throttle at scale.
"""

import csv
import io
import logging
import time
import zipfile
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://data.binance.vision"

# Reuse a single httpx client for all CSV downloads
_client = None
_client_lock = __import__('threading').Lock()


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(http2=True, timeout=30, follow_redirects=True)
        return _client

# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

def _spot_klines_daily_url(symbol, date_str, interval='1m'):
    """URL for daily spot klines CSV zip.

    Args:
        symbol: e.g. 'BTCUSDT'
        date_str: e.g. '2026-03-01'
        interval: e.g. '1m'
    """
    return (
        f"{BASE_URL}/data/spot/daily/klines/{symbol}/{interval}/"
        f"{symbol}-{interval}-{date_str}.zip"
    )


def _futures_metrics_daily_url(symbol, date_str):
    """URL for daily futures metrics CSV zip."""
    return (
        f"{BASE_URL}/data/futures/um/daily/metrics/{symbol}/"
        f"{symbol}-metrics-{date_str}.zip"
    )


def _funding_rate_monthly_url(symbol, year_month):
    """URL for monthly funding rate CSV zip.

    Args:
        symbol: e.g. 'BTCUSDT'
        year_month: e.g. '2026-03'
    """
    return (
        f"{BASE_URL}/data/futures/um/monthly/fundingRate/{symbol}/"
        f"{symbol}-fundingRate-{year_month}.zip"
    )


# ---------------------------------------------------------------------------
# Download + extract
# ---------------------------------------------------------------------------

def _download_and_extract_csv(url, max_retries=3):
    """Download a zip file and extract the first CSV file contents.

    Returns:
        str: raw CSV text, or None if download fails (404 etc.)
    """
    client = _get_client()
    for attempt in range(max_retries):
        try:
            response = client.get(url)
            if response.status_code == 404:
                logger.debug("Not found: %s", url)
                return None
            if response.status_code >= 500:
                wait = 2 ** (attempt + 1)
                logger.warning("Server error %d for %s, retrying in %ds",
                               response.status_code, url, wait)
                time.sleep(wait)
                continue
            response.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                csv_names = [n for n in zf.namelist() if n.endswith('.csv')]
                if not csv_names:
                    logger.warning("No CSV in zip: %s", url)
                    return None
                return zf.read(csv_names[0]).decode('utf-8')

        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
            wait = 2 ** (attempt + 1)
            logger.warning("Network error for %s: %s, retrying in %ds",
                           url, e, wait)
            time.sleep(wait)
            continue

    logger.warning("Failed to download %s after %d retries", url, max_retries)
    return None


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def _parse_timestamp_ms_or_us(value):
    """Parse Binance timestamp that may be milliseconds (13 digits) or
    microseconds (16 digits). Returns UTC-aware datetime.

    Pre-2025 spot kline CSVs use ms, 2025+ use μs.
    """
    ts_str = str(value).strip()
    ts_int = int(ts_str)
    if len(ts_str) >= 16:
        # Microseconds
        return datetime.fromtimestamp(ts_int / 1_000_000, tz=timezone.utc)
    else:
        # Milliseconds
        return datetime.fromtimestamp(ts_int / 1_000, tz=timezone.utc)


def _parse_datetime_string(value):
    """Parse 'YYYY-MM-DD HH:MM:SS' datetime string to UTC-aware datetime."""
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )


# ---------------------------------------------------------------------------
# Public fetch functions — return (rows, metadata)
# ---------------------------------------------------------------------------

def fetch_spot_klines_csv(symbol, date_str, interval='1m'):
    """Fetch one day of spot kline data from CSV download.

    Args:
        symbol: e.g. 'BTCUSDT'
        date_str: e.g. '2026-03-01'
        interval: candle interval (default '1m')

    Returns:
        Tuple of (rows, metadata).
        rows: list of dicts with keys:
            timestamp, open, high, low, close, volume, close_time,
            quote_volume, trade_count, taker_buy_volume, taker_buy_quote_volume
        metadata: dict with api_calls, source, date
    """
    url = _spot_klines_daily_url(symbol, date_str, interval)
    csv_text = _download_and_extract_csv(url)
    meta = {'api_calls': 1, 'source': 'binance_csv', 'date': date_str}

    if csv_text is None:
        return [], meta

    rows = []
    # Spot klines CSV has no header
    reader = csv.reader(io.StringIO(csv_text))
    for row in reader:
        if len(row) < 11:
            continue
        rows.append({
            'timestamp': _parse_timestamp_ms_or_us(row[0]),
            'open': row[1],
            'high': row[2],
            'low': row[3],
            'close': row[4],
            'volume': row[5],
            'close_time': row[6],
            'quote_volume': row[7],
            'trade_count': int(row[8]),
            'taker_buy_volume': row[9],
            'taker_buy_quote_volume': row[10],
        })

    logger.info(
        "Fetched %d spot klines for %s %s (%d API calls)",
        len(rows), symbol, date_str, meta['api_calls'],
    )
    return rows, meta


def fetch_futures_metrics_csv(symbol, date_str):
    """Fetch one day of futures metrics from CSV download.

    Returns:
        Tuple of (rows, metadata).
        rows: list of dicts with keys matching CSV header.
    """
    url = _futures_metrics_daily_url(symbol, date_str)
    csv_text = _download_and_extract_csv(url)
    meta = {'api_calls': 1, 'source': 'binance_csv', 'date': date_str}

    if csv_text is None:
        return [], meta

    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        row['timestamp'] = _parse_datetime_string(row['create_time'])
        rows.append(row)

    logger.info(
        "Fetched %d futures metrics for %s %s (%d API calls)",
        len(rows), symbol, date_str, meta['api_calls'],
    )
    return rows, meta


def fetch_funding_rate_csv(symbol, year_month):
    """Fetch one month of funding rate data from CSV download.

    Args:
        symbol: e.g. 'BTCUSDT'
        year_month: e.g. '2026-03'

    Returns:
        Tuple of (rows, metadata).
    """
    url = _funding_rate_monthly_url(symbol, year_month)
    csv_text = _download_and_extract_csv(url)
    meta = {'api_calls': 1, 'source': 'binance_csv', 'month': year_month}

    if csv_text is None:
        return [], meta

    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        row['timestamp'] = _parse_timestamp_ms_or_us(row['calc_time'])
        row['symbol'] = symbol  # Not in CSV — inferred from filename
        rows.append(row)

    logger.info(
        "Fetched %d funding rates for %s %s (%d API calls)",
        len(rows), symbol, year_month, meta['api_calls'],
    )
    return rows, meta
