"""Universal run_for_coin() — executes the 14-step pipeline scaffolding once."""

import logging
from datetime import datetime, timezone

from django.db.models import Max
from django.utils import timezone as dj_timezone

from warehouse.models import (
    MigratedCoin, PipelineCompleteness, PoolMapping,
    RunMode, RunStatus, U001PipelineRun, U001PipelineStatus,
)

logger = logging.getLogger(__name__)


def run_for_coin(spec, mint_address, start=None, end=None, **kwargs):
    """Execute a full pipeline run for one coin.

    Args:
        spec: PipelineSpec instance defining the pipeline.
        mint_address: Token mint address.
        start: Optional start datetime (refill mode).
        end: Optional end datetime (refill mode).
        **kwargs: Extra arguments forwarded to spec callables
                  (e.g. source, parse_workers).

    Returns:
        dict with 'status', 'records_loaded', 'records_skipped',
        'api_calls', 'mode', 'run_id', 'error_message'.
    """
    # Step 1: Coin lookup
    coin = MigratedCoin.objects.get(mint_address=mint_address)
    kwargs['coin'] = coin

    # Step 2: Pool lookup if needed
    pool_address = None
    if spec.requires_pool:
        pool = PoolMapping.objects.filter(
            coin_id=mint_address,
        ).order_by('created_at').first()
        if not pool:
            raise ValueError(
                f"No PoolMapping for {mint_address}. "
                f"Run populate_pool_mapping first."
            )
        pool_address = pool.pool_address

    # Step 3: Mode detection from watermark + overlap
    if start and end:
        mode = RunMode.REFILL
        logger.info("Re-fill mode: %s to %s for %s", start, end, mint_address)
    else:
        if coin.anchor_event is None:
            raise ValueError("Coin has no anchor_event set")

        watermark = _query_watermark(spec.model, mint_address)
        window_end = coin.anchor_event + MigratedCoin.OBSERVATION_WINDOW_END
        now = datetime.now(timezone.utc)
        end = min(window_end, now)

        if watermark is None:
            start = coin.anchor_event
            mode = RunMode.BOOTSTRAP
            logger.info(
                "Bootstrap mode: %s to %s for %s", start, end, mint_address,
            )
        else:
            start = max(watermark - spec.overlap, coin.anchor_event)
            mode = RunMode.STEADY_STATE
            logger.info(
                "Steady-state mode: %s to %s (overlap=%s) for %s",
                start, end, spec.overlap, mint_address,
            )

    # Step 4: Pre-flight hook (runs BEFORE creating run — no phantom runs)
    if spec.pre_flight:
        # Filter 'coin' from kwargs — it's passed positionally
        pf_kw = {k: v for k, v in kwargs.items() if k != 'coin'}
        spec.pre_flight(coin, pool_address, start, end, **pf_kw)

    # Step 5: Create pipeline run + mark IN_PROGRESS
    layer_id = spec.layer_id
    run = U001PipelineRun.objects.create(
        coin_id=mint_address,
        layer_id=layer_id,
        mode=mode,
        status=RunStatus.STARTED,
        started_at=dj_timezone.now(),
        time_range_start=start,
        time_range_end=end,
    )
    U001PipelineStatus.objects.update_or_create(
        coin_id=mint_address, layer_id=layer_id,
        defaults={'status': PipelineCompleteness.IN_PROGRESS,
                  'last_run_at': dj_timezone.now()},
    )

    # Step 6: Fetch
    try:
        raw, meta = spec.fetch(mint_address, pool_address, start, end, **kwargs)
    except Exception as e:
        logger.error("Connector failed for %s", mint_address, exc_info=True)
        _mark_error(run, layer_id, mint_address, e)
        raise RuntimeError(f"Connector failed for {mint_address}") from e

    # Step 7: Empty raw check
    if not raw:
        logger.info(
            "Zero results from API for %s in [%s, %s]",
            mint_address, start, end,
        )
        return _mark_complete_empty(spec, coin, run, layer_id, mint_address,
                                    meta, mode)

    logger.info("Received %d raw records for %s", len(raw), mint_address)

    # Step 8: Conform
    try:
        result = spec.conform(raw, mint_address, pool_address, **kwargs)
        if spec.conform_returns_tuple:
            canonical, skipped = result
        else:
            canonical, skipped = result, []
    except Exception as e:
        logger.error(
            "Conformance failed for %s (%d raw records)",
            mint_address, len(raw), exc_info=True,
        )
        _mark_error(run, layer_id, mint_address, e, meta=meta)
        raise RuntimeError(f"Conformance failed for {mint_address}") from e

    # Step 9: Empty canonical check
    if not canonical:
        logger.warning(
            "All %d records filtered during conformance for %s",
            len(raw), mint_address,
        )
        return _mark_complete_empty(spec, coin, run, layer_id, mint_address,
                                    meta, mode)

    # Step 10: Load
    spec.load(mint_address, start, end, canonical, skipped)

    # Step 11: Reconcile
    reconcile_data = {}
    if spec.reconcile:
        reconcile_data = spec.reconcile(
            canonical, skipped, start, end, meta, mint_address, **kwargs,
        )

    # Step 12: Update run
    run.status = RunStatus.COMPLETE
    run.completed_at = dj_timezone.now()
    run.records_loaded = len(canonical)
    run.api_calls = meta.get('api_calls', 0)
    run.cu_consumed = meta.get('cu_consumed', 0)
    if 'records_expected' in reconcile_data:
        run.records_expected = reconcile_data['records_expected']
    run.save()

    # Step 13: Watermark
    new_watermark = _query_watermark(spec.model, mint_address)

    # Step 14: Completeness + update status
    if spec.compute_completeness:
        completeness = spec.compute_completeness(
            coin, mint_address, new_watermark,
        )
    else:
        completeness = _default_completeness(spec, coin, new_watermark)

    U001PipelineStatus.objects.update_or_create(
        coin_id=mint_address, layer_id=layer_id,
        defaults={
            'status': completeness,
            'watermark': new_watermark,
            'last_run': run,
            'last_run_at': run.completed_at,
            'last_error': None,
        },
    )

    return {
        'status': completeness,
        'records_loaded': len(canonical),
        'records_skipped': len(skipped),
        'api_calls': meta.get('api_calls', 0),
        'mode': mode,
        'run_id': run.id,
        'error_message': None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mark_error(run, layer_id, mint_address, error, meta=None):
    """Mark run as ERROR and update pipeline status."""
    run.status = RunStatus.ERROR
    run.completed_at = dj_timezone.now()
    run.error_message = str(error)
    if meta:
        run.api_calls = meta.get('api_calls', 0)
        run.cu_consumed = meta.get('cu_consumed', 0)
    run.save()
    U001PipelineStatus.objects.update_or_create(
        coin_id=mint_address, layer_id=layer_id,
        defaults={
            'status': PipelineCompleteness.ERROR,
            'last_run': run,
            'last_run_at': run.completed_at,
            'last_error': str(error),
        },
    )


def _mark_complete_empty(spec, coin, run, layer_id, mint_address, meta, mode):
    """Mark run complete with 0 records and compute completeness."""
    run.status = RunStatus.COMPLETE
    run.completed_at = dj_timezone.now()
    run.records_loaded = 0
    run.api_calls = meta.get('api_calls', 0)
    run.cu_consumed = meta.get('cu_consumed', 0)
    run.save()

    if spec.compute_completeness:
        completeness = spec.compute_completeness(coin, mint_address)
    else:
        completeness = _default_completeness(spec, coin)

    U001PipelineStatus.objects.update_or_create(
        coin_id=mint_address, layer_id=layer_id,
        defaults={
            'status': completeness,
            'last_run': run,
            'last_run_at': run.completed_at,
            'last_error': None,
        },
    )

    return {
        'status': completeness, 'records_loaded': 0,
        'records_skipped': 0, 'api_calls': meta.get('api_calls', 0),
        'mode': mode, 'run_id': run.id, 'error_message': None,
    }


def _default_completeness(spec, coin, watermark=None):
    """Default completeness for feature layers and reference tables."""
    if watermark is None:
        watermark = _query_watermark(spec.model, coin.mint_address)

    temporal_resolution = getattr(spec.model, 'TEMPORAL_RESOLUTION', None)

    if temporal_resolution and watermark:
        # Feature layer: watermark near window end
        if watermark >= coin.window_end_time - temporal_resolution:
            return PipelineCompleteness.WINDOW_COMPLETE
    elif watermark:
        # Reference table: watermark past window end
        if coin.window_end_time and watermark >= coin.window_end_time:
            return PipelineCompleteness.WINDOW_COMPLETE

    if coin.is_mature:
        return PipelineCompleteness.WINDOW_COMPLETE

    return PipelineCompleteness.PARTIAL


def _query_watermark(model, mint_address):
    """Return MAX(timestamp) for a mint, or None."""
    return model.objects.filter(
        coin_id=mint_address,
    ).aggregate(Max('timestamp'))['timestamp__max']
