"""U-002 FL-003 pipeline spec: Futures metrics from Binance CSV.

Self-limiting: fetches at most 1 day per run_for_coin call.
"""

import logging
from datetime import timedelta

from pipeline.spec import PipelineSpec

logger = logging.getLogger(__name__)

MAX_FETCH = timedelta(days=1)


def _fetch(symbol, pool, start, end, **kw):
    """Fetch futures metrics CSV — 1 day per call."""
    from pipeline.connectors.binance_csv import fetch_futures_metrics_csv
    capped_end = min(end, start + MAX_FETCH)
    rows, meta = fetch_futures_metrics_csv(symbol, start.date().isoformat())
    rows = [r for r in rows if start <= r['timestamp'] <= capped_end]
    return rows, meta


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
