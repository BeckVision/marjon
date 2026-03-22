"""FL-001 pipeline spec: OHLCV candles from GeckoTerminal."""

import logging
from datetime import timedelta

from pipeline.spec import PipelineSpec

logger = logging.getLogger(__name__)


def _fetch(mint, pool, start, end, **kw):
    import pipeline.management.commands.fetch_ohlcv as cmd
    return cmd.fetch_ohlcv(pool, start, end)


def _conform(raw, mint, pool, **kw):
    import pipeline.management.commands.fetch_ohlcv as cmd
    return cmd.conform(raw, mint)


def _load(mint, start, end, canonical, skipped):
    from pipeline.loaders.common import delete_write
    from warehouse.models import OHLCVCandle
    delete_write(OHLCVCandle, mint, start, end, canonical)


def _reconcile(canonical, skipped, start, end, meta, mint, **kw):
    from warehouse.models import OHLCVCandle
    resolution_secs = OHLCVCandle.TEMPORAL_RESOLUTION.total_seconds()
    expected_intervals = (end - start).total_seconds() / resolution_secs
    timestamps = [r['timestamp'] for r in canonical]
    first_ts = min(timestamps)
    last_ts = max(timestamps)
    logger.info(
        "Reconciliation for %s: loaded=%d, theoretical_max=%.0f, "
        "first=%s, last=%s",
        mint, len(canonical), expected_intervals, first_ts, last_ts,
    )
    return {'records_expected': int(expected_intervals)}


def _build_spec():
    from warehouse.models import OHLCVCandle
    return PipelineSpec(
        layer_id='FL-001',
        model=OHLCVCandle,
        overlap=timedelta(minutes=30),
        fetch=_fetch,
        conform=_conform,
        load=_load,
        requires_pool=True,
        reconcile=_reconcile,
    )


FL001 = _build_spec()
