"""Visualization views — chart page and JSON API endpoints for U-002 data."""

from datetime import datetime, timedelta, timezone

from django.http import JsonResponse
from django.shortcuts import render

from warehouse.models import (
    BinanceAsset, U002FundingRate, U002FuturesMetrics, U002OHLCVCandle,
)

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
DEFAULT_DAYS = 7
MAX_CANDLES = 100_000


def _parse_range(request):
    """Parse start/end/days from query params."""
    end_str = request.GET.get('end')
    start_str = request.GET.get('start')
    days = request.GET.get('days')
    limit = int(request.GET.get('limit', 0))

    if end_str:
        end = datetime.fromisoformat(end_str)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
    else:
        end = datetime.now(timezone.utc)

    if start_str:
        start = datetime.fromisoformat(start_str)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
    elif days:
        start = end - timedelta(days=int(days))
    else:
        start = end - timedelta(days=DEFAULT_DAYS)

    return start, end, limit


def chart_view(request, symbol):
    """Render the chart page."""
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        symbol = SYMBOLS[0]
    return render(request, 'visualization/chart.html', {
        'symbol': symbol,
        'symbols': SYMBOLS,
    })


def klines_api(request, symbol):
    """JSON API: OHLCV candlestick + volume data."""
    symbol = symbol.upper()
    start, end, limit = _parse_range(request)

    # If no explicit end and default range has no data, snap to latest available
    if not request.GET.get('end'):
        from django.db.models import Max
        latest = U002OHLCVCandle.objects.filter(
            asset_id=symbol,
        ).aggregate(Max('timestamp'))['timestamp__max']
        if latest and latest < end:
            end = latest
            days = request.GET.get('days')
            start = end - timedelta(days=int(days) if days else DEFAULT_DAYS)

    qs = U002OHLCVCandle.objects.filter(
        asset_id=symbol,
        timestamp__gte=start,
        timestamp__lte=end,
    ).order_by('timestamp')

    if limit:
        qs = qs[:limit]
    elif qs.count() > MAX_CANDLES:
        qs = qs[:MAX_CANDLES]

    candles = []
    volume = []
    for row in qs.iterator():
        ts = int(row.timestamp.timestamp())
        o = float(row.open_price) if row.open_price else 0
        h = float(row.high_price) if row.high_price else 0
        l = float(row.low_price) if row.low_price else 0
        c = float(row.close_price) if row.close_price else 0
        v = float(row.volume) if row.volume else 0

        candles.append({'time': ts, 'open': o, 'high': h, 'low': l, 'close': c})
        color = 'rgba(38,166,154,0.5)' if c >= o else 'rgba(239,83,80,0.5)'
        volume.append({'time': ts, 'value': v, 'color': color})

    return JsonResponse({
        'symbol': symbol,
        'count': len(candles),
        'candles': candles,
        'volume': volume,
    })


def metrics_api(request, symbol):
    """JSON API: Futures metrics (OI + long/short ratio)."""
    symbol = symbol.upper()
    start, end, limit = _parse_range(request)

    if not request.GET.get('end'):
        from django.db.models import Max
        latest = U002FuturesMetrics.objects.filter(
            asset_id=symbol,
        ).aggregate(Max('timestamp'))['timestamp__max']
        if latest and latest < end:
            end = latest
            days = request.GET.get('days')
            start = end - timedelta(days=int(days) if days else DEFAULT_DAYS)

    qs = U002FuturesMetrics.objects.filter(
        asset_id=symbol,
        timestamp__gte=start,
        timestamp__lte=end,
    ).order_by('timestamp')

    if limit:
        qs = qs[:limit]

    open_interest = []
    long_short_ratio = []
    for row in qs.iterator():
        ts = int(row.timestamp.timestamp())
        if row.sum_open_interest_value is not None:
            open_interest.append({
                'time': ts,
                'value': float(row.sum_open_interest_value),
            })
        if row.count_long_short_ratio is not None:
            long_short_ratio.append({
                'time': ts,
                'value': float(row.count_long_short_ratio),
            })

    return JsonResponse({
        'symbol': symbol,
        'count': len(open_interest),
        'open_interest': open_interest,
        'long_short_ratio': long_short_ratio,
    })


def funding_api(request, symbol):
    """JSON API: Funding rate."""
    symbol = symbol.upper()
    start, end, limit = _parse_range(request)

    if not request.GET.get('end'):
        from django.db.models import Max
        latest = U002FundingRate.objects.filter(
            asset_id=symbol,
        ).aggregate(Max('timestamp'))['timestamp__max']
        if latest and latest < end:
            end = latest
            days = request.GET.get('days')
            start = end - timedelta(days=int(days) if days else DEFAULT_DAYS)

    qs = U002FundingRate.objects.filter(
        asset_id=symbol,
        timestamp__gte=start,
        timestamp__lte=end,
    ).order_by('timestamp')

    if limit:
        qs = qs[:limit]

    funding = []
    for row in qs.iterator():
        ts = int(row.timestamp.timestamp())
        rate = float(row.last_funding_rate) if row.last_funding_rate else 0
        color = 'rgba(38,166,154,0.8)' if rate >= 0 else 'rgba(239,83,80,0.8)'
        funding.append({'time': ts, 'value': rate, 'color': color})

    return JsonResponse({
        'symbol': symbol,
        'count': len(funding),
        'funding': funding,
    })
