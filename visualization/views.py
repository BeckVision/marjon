"""Visualization views — landing page, chart page, and JSON API endpoints."""

from datetime import datetime, timedelta, timezone

from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render

from .u001_ops import (
    build_automation_history_summary,
    build_coin_detail_summary,
    build_coverage_summary,
    build_overview_summary,
    build_queue_summary,
    build_trends_summary,
)
from warehouse.models import (
    BinanceAsset, MigratedCoin, U002FundingRate, U002FuturesMetrics, U002OHLCVCandle,
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


def _snap_to_latest(model, symbol, start, end, request):
    """If no explicit end and data doesn't reach 'now', snap to latest available."""
    if request.GET.get('end'):
        return start, end
    latest = model.objects.filter(
        asset_id=symbol,
    ).aggregate(Max('timestamp'))['timestamp__max']
    if latest and latest < end:
        end = latest
        days = request.GET.get('days')
        start = end - timedelta(days=int(days) if days else DEFAULT_DAYS)
    return start, end


def home_view(request):
    """Render the product landing page."""
    return render(request, 'visualization/home.html', {
        'symbols': SYMBOLS,
        'default_symbol': SYMBOLS[0],
        'nav_active': 'home',
    })


def chart_view(request, symbol):
    """Render the chart page."""
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        symbol = SYMBOLS[0]
    return render(request, 'visualization/chart.html', {
        'symbol': symbol,
        'symbols': SYMBOLS,
        'nav_active': 'chart',
    })


def u001_ops_overview_view(request):
    """Render the first U-001 operations cockpit page."""
    return render(request, 'visualization/u001_ops_overview.html', {
        'summary': build_overview_summary(),
        'nav_active': 'ops',
        'ops_tab': 'overview',
    })


def u001_ops_summary_api(request):
    """JSON API: U-001 operations overview summary."""
    return JsonResponse(build_overview_summary())


def u001_ops_automation_view(request):
    """Render the U-001 automation history page."""
    return render(request, 'visualization/u001_ops_automation.html', {
        'summary': build_automation_history_summary(
            limit=request.GET.get('limit', '50'),
            action=request.GET.get('action') or None,
            status=request.GET.get('status') or None,
        ),
        'nav_active': 'ops',
        'ops_tab': 'automation',
    })


def u001_ops_automation_api(request):
    """JSON API: U-001 automation history summary."""
    return JsonResponse(build_automation_history_summary(
        limit=request.GET.get('limit', '50'),
        action=request.GET.get('action') or None,
        status=request.GET.get('status') or None,
    ))


def u001_ops_coverage_view(request):
    """Render the U-001 coverage funnel page."""
    preset = request.GET.get('preset', '1000')
    return render(request, 'visualization/u001_ops_coverage.html', {
        'summary': build_coverage_summary(preset=preset),
        'nav_active': 'ops',
        'ops_tab': 'coverage',
    })


def u001_ops_coverage_api(request):
    """JSON API: U-001 coverage funnel summary."""
    preset = request.GET.get('preset', '1000')
    return JsonResponse(build_coverage_summary(preset=preset))


def u001_ops_queues_view(request):
    """Render the U-001 queue planner page."""
    return render(request, 'visualization/u001_ops_queues.html', {
        'summary': build_queue_summary(),
        'nav_active': 'ops',
        'ops_tab': 'queues',
    })


def u001_ops_queues_api(request):
    """JSON API: U-001 queue planner summary."""
    return JsonResponse(build_queue_summary())


def u001_ops_coin_view(request, mint):
    """Render the U-001 single-coin debug page."""
    get_object_or_404(MigratedCoin, mint_address=mint)
    return render(request, 'visualization/u001_ops_coin.html', {
        'summary': build_coin_detail_summary(mint),
        'nav_active': 'ops',
        'ops_tab': 'coin',
    })


def u001_ops_coin_api(request, mint):
    """JSON API: U-001 single-coin debug summary."""
    get_object_or_404(MigratedCoin, mint_address=mint)
    return JsonResponse(build_coin_detail_summary(mint))


def u001_ops_trends_view(request):
    """Render the U-001 trends page."""
    days = request.GET.get('days', '14')
    return render(request, 'visualization/u001_ops_trends.html', {
        'summary': build_trends_summary(days=days),
        'nav_active': 'ops',
        'ops_tab': 'trends',
    })


def u001_ops_trends_api(request):
    """JSON API: U-001 trends summary."""
    days = request.GET.get('days', '14')
    return JsonResponse(build_trends_summary(days=days))


def klines_api(request, symbol):
    """JSON API: OHLCV candlestick + volume data."""
    symbol = symbol.upper()
    start, end, limit = _parse_range(request)
    start, end = _snap_to_latest(U002OHLCVCandle, symbol, start, end, request)

    cap = limit if limit else MAX_CANDLES
    qs = U002OHLCVCandle.objects.filter(
        asset_id=symbol,
        timestamp__gte=start,
        timestamp__lte=end,
    ).order_by('timestamp').only(
        'timestamp', 'open_price', 'high_price', 'low_price', 'close_price', 'volume',
    )[:cap]

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
    start, end = _snap_to_latest(U002FuturesMetrics, symbol, start, end, request)

    cap = limit if limit else MAX_CANDLES
    qs = U002FuturesMetrics.objects.filter(
        asset_id=symbol,
        timestamp__gte=start,
        timestamp__lte=end,
    ).order_by('timestamp').only(
        'timestamp', 'sum_open_interest_value', 'count_long_short_ratio',
    )[:cap]

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
    start, end = _snap_to_latest(U002FundingRate, symbol, start, end, request)

    cap = limit if limit else MAX_CANDLES
    qs = U002FundingRate.objects.filter(
        asset_id=symbol,
        timestamp__gte=start,
        timestamp__lte=end,
    ).order_by('timestamp').only(
        'timestamp', 'last_funding_rate',
    )[:cap]

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
