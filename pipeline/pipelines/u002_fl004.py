"""U-002 FL-004 pipeline spec: Funding rate from Binance CSV (monthly files).

Self-limiting: fetches at most 1 month per run_for_coin call.
Funding rate has ~3 entries/day (every 8h), so 1 month ≈ 93 rows — trivial.
"""

import logging
from datetime import timedelta

from pipeline.spec import PipelineSpec

logger = logging.getLogger(__name__)

# Funding rate comes in monthly files — fetch 1 month at a time
MAX_FETCH = timedelta(days=31)


def _fetch(symbol, pool, start, end, **kw):
    """Fetch funding rate CSV — 1 month per call."""
    from pipeline.connectors.binance_csv import fetch_funding_rate_csv
    capped_end = min(end, start + MAX_FETCH)
    all_rows = []
    total_calls = 0
    # Iterate months that overlap [start, capped_end]
    cy, cm = start.year, start.month
    ey, em = capped_end.year, capped_end.month
    while (cy, cm) <= (ey, em):
        year_month = f"{cy}-{cm:02d}"
        rows, meta = fetch_funding_rate_csv(symbol, year_month)
        all_rows.extend(rows)
        total_calls += meta['api_calls']
        cm += 1
        if cm > 12:
            cy += 1
            cm = 1
    all_rows = [r for r in all_rows if start <= r['timestamp'] <= capped_end]
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
