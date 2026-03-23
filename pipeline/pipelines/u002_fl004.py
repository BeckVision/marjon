"""U-002 FL-004 pipeline spec: Funding rate from Binance CSV (monthly files)."""

import logging
from datetime import timedelta

from pipeline.spec import PipelineSpec

logger = logging.getLogger(__name__)


def _fetch(symbol, pool, start, end, **kw):
    """Fetch funding rate CSV — one file per month."""
    from pipeline.connectors.binance_csv import fetch_funding_rate_csv
    all_rows = []
    total_calls = 0
    # Iterate months in range
    current_year = start.year
    current_month = start.month
    while (current_year, current_month) <= (end.year, end.month):
        year_month = f"{current_year}-{current_month:02d}"
        rows, meta = fetch_funding_rate_csv(symbol, year_month)
        # Filter rows to the requested range
        rows = [r for r in rows if start <= r['timestamp'] <= end]
        all_rows.extend(rows)
        total_calls += meta['api_calls']
        # Advance to next month
        if current_month == 12:
            current_year += 1
            current_month = 1
        else:
            current_month += 1
    return all_rows, {'api_calls': total_calls, 'source': 'binance_csv'}


def _conform(raw, symbol, pool, **kw):
    from pipeline.conformance.u002_fl004_binance import conform
    return conform(raw, symbol)


def _load(symbol, start, end, canonical, skipped):
    from pipeline.loaders.u002_fl004 import load
    load(symbol, start, end, canonical)


def _build_spec():
    from warehouse.models import (
        BinanceAsset, U002FundingRate,
        U002PipelineRun, U002PipelineStatus,
    )
    return PipelineSpec(
        layer_id='U002-FL-004',
        model=U002FundingRate,
        overlap=timedelta(hours=16),
        fetch=_fetch,
        conform=_conform,
        load=_load,
        requires_pool=False,
        universe_model=BinanceAsset,
        asset_field='symbol',
        run_model=U002PipelineRun,
        status_model=U002PipelineStatus,
    )


U002_FL004 = _build_spec()
