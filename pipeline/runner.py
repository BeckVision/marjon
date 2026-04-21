"""Universal run_for_coin() — executes the 14-step pipeline scaffolding once.

Universe-agnostic: uses spec.universe_model, spec.run_model, spec.status_model
to resolve models. Falls back to U-001 models when spec fields are None
(backward compatibility).
"""

import logging
from datetime import datetime, timezone

from django.utils import timezone as dj_timezone

from warehouse.models import (
    MigratedCoin, PipelineCompleteness, PoolMapping,
    RunMode, RunStatus, U001PipelineRun, U001PipelineStatus,
)
from pipeline.loaders.common import get_watermark
from warehouse.utils import find_universe_fk

logger = logging.getLogger(__name__)


def _resolve_models(spec):
    """Resolve universe/run/status models from spec, falling back to U-001."""
    universe_model = spec.universe_model or MigratedCoin
    run_model = spec.run_model or U001PipelineRun
    status_model = spec.status_model or U001PipelineStatus
    return universe_model, run_model, status_model


def run_for_coin(spec, asset_id, start=None, end=None, **kwargs):
    """Execute a full pipeline run for one asset.

    Args:
        spec: PipelineSpec instance defining the pipeline.
        asset_id: Asset identifier (e.g. mint_address, symbol).
        start: Optional start datetime (refill mode).
        end: Optional end datetime (refill mode).
        **kwargs: Extra arguments forwarded to spec callables
                  (e.g. source, parse_workers).

    Returns:
        dict with 'status', 'records_loaded', 'records_skipped',
        'api_calls', 'mode', 'run_id', 'error_message'.
    """
    universe_model, run_model, status_model = _resolve_models(spec)
    asset_field = spec.asset_field
    run_fk = find_universe_fk(run_model)
    status_fk = find_universe_fk(status_model)
    feature_fk = find_universe_fk(spec.model)

    # Step 1: Asset lookup
    asset = universe_model.objects.get(**{asset_field: asset_id})
    kwargs['coin'] = asset  # backward compat key name

    # Step 2: Pool lookup if needed
    pool_address = None
    if spec.requires_pool:
        pool = PoolMapping.objects.filter(
            coin_id=asset_id,
        ).order_by('created_at').first()
        if not pool:
            raise ValueError(
                f"No PoolMapping for {asset_id}. "
                f"Run populate_pool_mapping first."
            )
        pool_address = pool.pool_address

    # Step 3: Mode detection from watermark + window properties
    if start and end:
        mode = RunMode.REFILL
        logger.info("Re-fill mode: %s to %s for %s", start, end, asset_id)
    else:
        ws = asset.window_start_time
        we = asset.window_end_time

        if ws is None:
            raise ValueError(
                f"Cannot determine window start for {asset_id} — "
                f"event-driven asset missing anchor_event, or "
                f"calendar-driven universe has no OBSERVATION_WINDOW_START"
            )

        watermark = get_watermark(spec.model, asset_id, asset_fk=feature_fk)
        now = datetime.now(timezone.utc)
        end = min(we, now) if we is not None else now

        if watermark is None:
            start = ws
            mode = RunMode.BOOTSTRAP
            logger.info(
                "Bootstrap mode: %s to %s for %s", start, end, asset_id,
            )
        else:
            start = max(watermark - spec.overlap, ws)
            mode = RunMode.STEADY_STATE
            logger.info(
                "Steady-state mode: %s to %s (overlap=%s) for %s",
                start, end, spec.overlap, asset_id,
            )

    # Step 4: Pre-flight hook (runs BEFORE creating run — no phantom runs)
    if spec.pre_flight:
        pf_kw = {k: v for k, v in kwargs.items() if k != 'coin'}
        spec.pre_flight(asset, pool_address, start, end, **pf_kw)

    # Step 5: Create pipeline run + mark IN_PROGRESS
    layer_id = spec.layer_id
    try:
        prior_status = status_model.objects.get(
            **{status_fk: asset_id}, layer_id=layer_id,
        ).status
    except status_model.DoesNotExist:
        prior_status = None
    run = run_model.objects.create(
        **{run_fk: asset_id},
        layer_id=layer_id,
        mode=mode,
        status=RunStatus.STARTED,
        started_at=dj_timezone.now(),
        time_range_start=start,
        time_range_end=end,
    )
    _update_status(status_model, status_fk, asset_id, layer_id, {
        'status': PipelineCompleteness.IN_PROGRESS,
        'last_run_at': dj_timezone.now(),
    })

    # Step 6A: Optional streaming fetch path
    if spec.fetch_stream and spec.load_chunk:
        try:
            raw_stream = spec.fetch_stream(
                asset_id, pool_address, start, end, **kwargs,
            )
        except Exception as e:
            logger.error("Connector failed for %s", asset_id, exc_info=True)
            _mark_error(
                run, status_model, status_fk, layer_id, asset_id, e,
                prior_status=prior_status,
            )
            raise RuntimeError(f"Connector failed for {asset_id}") from e

        if raw_stream is not None:
            return _run_streaming_for_coin(
                spec, asset, asset_id, pool_address, start, end, kwargs,
                run, status_model, status_fk, feature_fk, layer_id, mode,
                prior_status, raw_stream,
            )

    # Step 6: Fetch
    try:
        raw, meta = spec.fetch(asset_id, pool_address, start, end, **kwargs)
    except Exception as e:
        logger.error("Connector failed for %s", asset_id, exc_info=True)
        _mark_error(
            run, status_model, status_fk, layer_id, asset_id, e,
            prior_status=prior_status,
        )
        raise RuntimeError(f"Connector failed for {asset_id}") from e

    # Step 7: Empty raw check
    if not raw:
        logger.info(
            "Zero results from API for %s in [%s, %s]",
            asset_id, start, end,
        )
        return _mark_complete_empty(spec, asset, run, status_model, status_fk,
                                    feature_fk, layer_id, asset_id, meta, mode)

    logger.info("Received %d raw records for %s", len(raw), asset_id)

    # Step 8: Conform
    try:
        result = spec.conform(raw, asset_id, pool_address, **kwargs)
        if spec.conform_returns_tuple:
            canonical, skipped = result
        else:
            canonical, skipped = result, []
    except Exception as e:
        logger.error(
            "Conformance failed for %s (%d raw records)",
            asset_id, len(raw), exc_info=True,
        )
        _mark_error(
            run, status_model, status_fk, layer_id, asset_id, e,
            meta=meta, prior_status=prior_status,
        )
        raise RuntimeError(f"Conformance failed for {asset_id}") from e

    # Step 9: Empty canonical check
    if not canonical:
        logger.warning(
            "All %d records filtered during conformance for %s",
            len(raw), asset_id,
        )
        return _mark_complete_empty(spec, asset, run, status_model, status_fk,
                                    feature_fk, layer_id, asset_id, meta, mode)

    # Step 10: Load
    spec.load(asset_id, start, end, canonical, skipped)

    # Step 11: Reconcile
    reconcile_data = {}
    if spec.reconcile:
        reconcile_data = spec.reconcile(
            canonical, skipped, start, end, meta, asset_id, **kwargs,
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
    new_watermark = get_watermark(spec.model, asset_id, asset_fk=feature_fk)

    # Step 14: Completeness + update status
    if spec.compute_completeness:
        completeness = spec.compute_completeness(
            asset, asset_id, new_watermark,
        )
    else:
        completeness = _default_completeness(spec, asset, feature_fk, new_watermark)

    _update_status(status_model, status_fk, asset_id, layer_id, {
        'status': completeness,
        'watermark': new_watermark,
        'last_run': run,
        'last_run_at': run.completed_at,
        'last_error': None,
    })

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

def _update_status(status_model, status_fk, asset_id, layer_id, defaults):
    """Update or create a pipeline status row."""
    status_model.objects.update_or_create(
        **{status_fk: asset_id}, layer_id=layer_id,
        defaults=defaults,
    )


def _run_streaming_for_coin(spec, asset, asset_id, pool_address, start, end,
                            kwargs, run, status_model, status_fk, feature_fk,
                            layer_id, mode, prior_status, raw_stream):
    """Execute a pipeline run using chunked fetch/conform/load."""
    total_loaded = 0
    total_skipped = 0
    saw_any_raw = False
    prepared = False
    pending_skipped = []

    try:
        for raw in raw_stream:
            if not raw:
                continue

            saw_any_raw = True
            logger.info(
                "Received %d raw records for %s (stream chunk)",
                len(raw), asset_id,
            )

            result = spec.conform(raw, asset_id, pool_address, **kwargs)
            if spec.conform_returns_tuple:
                canonical, skipped = result
            else:
                canonical, skipped = result, []

            if canonical:
                if not prepared:
                    if spec.prepare_load:
                        spec.prepare_load(asset_id, start, end)
                    prepared = True
                    if pending_skipped:
                        spec.load_chunk(asset_id, [], pending_skipped)
                        total_skipped += len(pending_skipped)
                        pending_skipped = []

                spec.load_chunk(asset_id, canonical, skipped)
                total_loaded += len(canonical)
                total_skipped += len(skipped)
                continue

            if skipped:
                if prepared:
                    spec.load_chunk(asset_id, [], skipped)
                    total_skipped += len(skipped)
                else:
                    pending_skipped.extend(skipped)
    except Exception as e:
        logger.error("Streaming pipeline failed for %s", asset_id, exc_info=True)
        _mark_error(
            run, status_model, status_fk, layer_id, asset_id, e,
            meta=getattr(raw_stream, 'meta', None), prior_status=prior_status,
        )
        raise RuntimeError(f"Streaming pipeline failed for {asset_id}") from e

    meta = getattr(raw_stream, 'meta', {})

    if not saw_any_raw:
        logger.info(
            "Zero results from API for %s in [%s, %s]",
            asset_id, start, end,
        )
        return _mark_complete_empty(
            spec, asset, run, status_model, status_fk, feature_fk,
            layer_id, asset_id, meta, mode,
        )

    if total_loaded == 0:
        logger.warning(
            "All streamed records filtered during conformance for %s",
            asset_id,
        )
        return _mark_complete_empty(
            spec, asset, run, status_model, status_fk, feature_fk,
            layer_id, asset_id, meta, mode,
        )

    reconcile_data = {}
    if spec.reconcile_stream:
        reconcile_data = spec.reconcile_stream(
            total_loaded, total_skipped, start, end, meta, asset_id, **kwargs,
        )

    run.status = RunStatus.COMPLETE
    run.completed_at = dj_timezone.now()
    run.records_loaded = total_loaded
    run.api_calls = meta.get('api_calls', 0)
    run.cu_consumed = meta.get('cu_consumed', 0)
    if 'records_expected' in reconcile_data:
        run.records_expected = reconcile_data['records_expected']
    run.save()

    new_watermark = get_watermark(spec.model, asset_id, asset_fk=feature_fk)

    if spec.compute_completeness:
        completeness = spec.compute_completeness(
            asset, asset_id, new_watermark,
        )
    else:
        completeness = _default_completeness(spec, asset, feature_fk, new_watermark)

    _update_status(status_model, status_fk, asset_id, layer_id, {
        'status': completeness,
        'watermark': new_watermark,
        'last_run': run,
        'last_run_at': run.completed_at,
        'last_error': None,
    })

    return {
        'status': completeness,
        'records_loaded': total_loaded,
        'records_skipped': total_skipped,
        'api_calls': meta.get('api_calls', 0),
        'mode': mode,
        'run_id': run.id,
        'error_message': None,
    }


def _mark_error(run, status_model, status_fk, layer_id, asset_id, error,
                meta=None, prior_status=None):
    """Mark run as ERROR and update pipeline status."""
    run.status = RunStatus.ERROR
    run.completed_at = dj_timezone.now()
    run.error_message = str(error)
    if meta:
        run.api_calls = meta.get('api_calls', 0)
        run.cu_consumed = meta.get('cu_consumed', 0)
    run.save()

    _update_status(status_model, status_fk, asset_id, layer_id, {
        'status': PipelineCompleteness.ERROR,
        'last_run': run,
        'last_run_at': run.completed_at,
        'last_error': str(error),
    })


def _mark_complete_empty(spec, asset, run, status_model, status_fk,
                         feature_fk, layer_id, asset_id, meta, mode):
    """Mark run complete with 0 records and compute completeness."""
    run.status = RunStatus.COMPLETE
    run.completed_at = dj_timezone.now()
    run.records_loaded = 0
    run.api_calls = meta.get('api_calls', 0)
    run.cu_consumed = meta.get('cu_consumed', 0)
    run.save()

    if spec.compute_completeness:
        completeness = spec.compute_completeness(asset, asset_id)
    else:
        completeness = _default_completeness(spec, asset, feature_fk)

    _update_status(status_model, status_fk, asset_id, layer_id, {
        'status': completeness,
        'last_run': run,
        'last_run_at': run.completed_at,
        'last_error': None,
    })

    return {
        'status': completeness, 'records_loaded': 0,
        'records_skipped': 0, 'api_calls': meta.get('api_calls', 0),
        'mode': mode, 'run_id': run.id, 'error_message': None,
    }


def _default_completeness(spec, asset, feature_fk, watermark=None):
    """Default completeness for feature layers and reference tables."""
    asset_field = spec.asset_field
    asset_id = getattr(asset, asset_field)
    if watermark is None:
        watermark = get_watermark(spec.model, asset_id, asset_fk=feature_fk)

    temporal_resolution = getattr(spec.model, 'TEMPORAL_RESOLUTION', None)
    we = asset.window_end_time

    if temporal_resolution and watermark and we:
        if watermark >= we - temporal_resolution:
            return PipelineCompleteness.WINDOW_COMPLETE
    elif watermark and we:
        if watermark >= we:
            return PipelineCompleteness.WINDOW_COMPLETE

    if asset.is_mature:
        return PipelineCompleteness.WINDOW_COMPLETE

    return PipelineCompleteness.PARTIAL
