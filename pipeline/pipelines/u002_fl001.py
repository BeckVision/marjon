"""U-002 FL-001 pipeline spec: Spot klines from Binance (CSV backfill + API steady-state)."""

import logging
from datetime import timedelta

from pipeline.spec import PipelineSpec

logger = logging.getLogger(__name__)


def _fetch(symbol, pool, start, end, **kw):
    """Fetch klines — CSV for backfill, API for steady-state."""
    source = kw.get('source', 'csv')
    if source == 'api':
        from pipeline.connectors.binance_api import fetch_klines_api
        return fetch_klines_api(symbol, start, end)
    else:
        # CSV mode: fetch one day at a time, aggregate
        from pipeline.connectors.binance_csv import fetch_spot_klines_csv
        from datetime import date as date_type
        all_rows = []
        total_calls = 0
        current = start.date()
        end_date = end.date()
        while current <= end_date:
            rows, meta = fetch_spot_klines_csv(symbol, current.isoformat())
            all_rows.extend(rows)
            total_calls += meta['api_calls']
            current += timedelta(days=1)
        return all_rows, {'api_calls': total_calls, 'source': 'binance_csv'}


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
