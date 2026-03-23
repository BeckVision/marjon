"""U-002 FL-001 pipeline spec: Spot klines from Binance (CSV backfill + API steady-state).

Self-limiting: fetches at most 1 day per run_for_coin call. Repeated
invocations (via orchestrate --loops) advance the watermark day by day.
"""

import logging
from datetime import timedelta

from pipeline.spec import PipelineSpec

logger = logging.getLogger(__name__)

# Max data fetched per run — keeps memory bounded and loads fast
MAX_FETCH = timedelta(days=1)


def _fetch(symbol, pool, start, end, **kw):
    """Fetch klines — at most 1 day per call."""
    # Cap to 1 day so each run is bounded
    capped_end = min(end, start + MAX_FETCH)

    source = kw.get('source', 'csv')
    if source == 'api':
        from pipeline.connectors.binance_api import fetch_klines_api
        return fetch_klines_api(symbol, start, capped_end)
    else:
        from pipeline.connectors.binance_csv import fetch_spot_klines_csv
        # 1 day = 1 CSV file
        rows, meta = fetch_spot_klines_csv(symbol, start.date().isoformat())
        # Filter to actual range (overlap region may pull earlier data)
        rows = [r for r in rows if start <= r['timestamp'] <= capped_end]
        return rows, meta


def _conform(raw, symbol, pool, **kw):
    from pipeline.conformance.u002_fl001_binance import conform
    return conform(raw, symbol)


def _load(symbol, start, end, canonical, skipped):
    from pipeline.loaders.u002_fl001 import load
    load(symbol, start, end, canonical)


def _reconcile(canonical, skipped, start, end, meta, symbol, **kw):
    expected = int((end - start).total_seconds() / 60)
    loaded = len(canonical)
    if loaded != expected:
        logger.info(
            "Reconciliation for %s: loaded=%d, expected=%d",
            symbol, loaded, expected,
        )
    return {'records_expected': expected}


def _build_spec():
    from warehouse.models import (
        BinanceAsset, U002OHLCVCandle,
        U002PipelineRun, U002PipelineStatus,
    )
    return PipelineSpec(
        layer_id='U002-FL-001',
        model=U002OHLCVCandle,
        overlap=timedelta(minutes=5),
        fetch=_fetch,
        conform=_conform,
        load=_load,
        requires_pool=False,
        reconcile=_reconcile,
        universe_model=BinanceAsset,
        asset_field='symbol',
        run_model=U002PipelineRun,
        status_model=U002PipelineStatus,
    )


U002_FL001 = _build_spec()
