"""U-002 FL-003 pipeline spec: Futures metrics from Binance CSV."""

import logging
from datetime import timedelta

from pipeline.spec import PipelineSpec

logger = logging.getLogger(__name__)


def _fetch(symbol, pool, start, end, **kw):
    """Fetch futures metrics CSV — one file per day."""
    from pipeline.connectors.binance_csv import fetch_futures_metrics_csv
    all_rows = []
    total_calls = 0
    current = start.date()
    end_date = end.date()
    while current <= end_date:
        rows, meta = fetch_futures_metrics_csv(symbol, current.isoformat())
        all_rows.extend(rows)
        total_calls += meta['api_calls']
        current += timedelta(days=1)
    return all_rows, {'api_calls': total_calls, 'source': 'binance_csv'}


def _conform(raw, symbol, pool, **kw):
    from pipeline.conformance.u002_fl003_binance import conform
    return conform(raw, symbol)


def _load(symbol, start, end, canonical, skipped):
    from pipeline.loaders.u002_fl003 import load
    load(symbol, start, end, canonical)


def _build_spec():
    from warehouse.models import (
        BinanceAsset, U002FuturesMetrics,
        U002PipelineRun, U002PipelineStatus,
    )
    return PipelineSpec(
        layer_id='U002-FL-003',
        model=U002FuturesMetrics,
        overlap=timedelta(minutes=10),
        fetch=_fetch,
        conform=_conform,
        load=_load,
        requires_pool=False,
        universe_model=BinanceAsset,
        asset_field='symbol',
        run_model=U002PipelineRun,
        status_model=U002PipelineStatus,
    )


U002_FL003 = _build_spec()
