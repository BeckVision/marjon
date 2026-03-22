"""Utility functions for the pipeline orchestrator."""

import importlib
import logging
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from warehouse.models import (
    MigratedCoin, PipelineCompleteness, PoolMapping,
    RunStatus, U001PipelineRun, U001PipelineStatus,
)

logger = logging.getLogger(__name__)


def load_universe_config(universe_id):
    """Load pipeline/universes/{universe_id}.py and return UNIVERSE dict."""
    module_path = f"pipeline.universes.{universe_id}"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        raise ValueError(
            f"No universe config found at {module_path}. "
            f"Create pipeline/universes/{universe_id}.py with a UNIVERSE dict."
        )
    if not hasattr(module, 'UNIVERSE'):
        raise ValueError(
            f"{module_path} does not define a UNIVERSE dict."
        )
    return module.UNIVERSE


def resolve_step_order(config, requested_steps=None):
    """Return steps in dependency order, filtered to requested_steps.

    Topological sort based on depends_on. Discovery is not in steps —
    it's handled separately by the orchestrator.
    """
    steps = config['steps']

    if requested_steps is not None:
        steps = [s for s in steps if s['name'] in requested_steps]

    # Build adjacency: step name -> list of steps that depend on it
    # For topological sort, we just need to respect depends_on ordering
    step_map = {s['name']: s for s in steps}
    in_degree = defaultdict(int)
    dependents = defaultdict(list)

    for s in steps:
        dep = s.get('depends_on')
        if dep and dep in step_map:
            in_degree[s['name']] += 1
            dependents[dep].append(s['name'])
        elif s['name'] not in in_degree:
            in_degree[s['name']] = in_degree.get(s['name'], 0)

    # Kahn's algorithm
    queue = [name for name in step_map if in_degree.get(name, 0) == 0]
    ordered = []
    while queue:
        name = queue.pop(0)
        ordered.append(step_map[name])
        for dep_name in dependents.get(name, []):
            in_degree[dep_name] -= 1
            if in_degree[dep_name] == 0:
                queue.append(dep_name)

    if len(ordered) != len(steps):
        raise ValueError(
            "Circular dependency detected in pipeline steps. "
            f"Ordered {len(ordered)} of {len(steps)} steps."
        )

    return ordered


def get_coins_to_process(config, days=None, max_coins=None):
    """Query MigratedCoin for coins that need processing.

    Args:
        config: Universe config dict.
        days: If set, only coins graduated in the last N days.
        max_coins: If set, limit result count.

    Returns:
        QuerySet of MigratedCoin, ordered by anchor_event descending.
    """
    qs = MigratedCoin.objects.all()

    if days is not None:
        cutoff = timezone.now() - timedelta(days=days)
        qs = qs.filter(anchor_event__gte=cutoff)

    qs = qs.order_by('-anchor_event')

    if max_coins is not None:
        qs = qs[:max_coins]

    return list(qs)


def has_consecutive_failures(coin, layer_id, max_failures):
    """Check if a coin has N consecutive ERROR runs for a layer.

    Queries last N U001PipelineRun rows ordered by -started_at.
    Returns True if all are ERROR status.
    """
    recent_runs = list(
        U001PipelineRun.objects.filter(
            coin_id=coin.mint_address,
            layer_id=layer_id,
        ).order_by('-started_at').values_list('status', flat=True)[:max_failures]
    )
    if len(recent_runs) < max_failures:
        return False
    return all(s == RunStatus.ERROR for s in recent_runs)


def get_persistent_failures(layer_ids, min_failures=5):
    """Find coins with consecutive failures across given layers.

    Scans U001PipelineStatus rows in ERROR state, then checks
    U001PipelineRun for consecutive error runs.

    Returns list of dicts: {coin_id, layer_id, consecutive_errors}.
    """
    results = []
    error_statuses = U001PipelineStatus.objects.filter(
        status=PipelineCompleteness.ERROR,
        layer_id__in=layer_ids,
    )
    for ps in error_statuses:
        recent_runs = list(
            U001PipelineRun.objects.filter(
                coin_id=ps.coin_id,
                layer_id=ps.layer_id,
            ).order_by('-started_at').values_list('status', flat=True)[:min_failures]
        )
        consecutive = 0
        for s in recent_runs:
            if s == RunStatus.ERROR:
                consecutive += 1
            else:
                break
        if consecutive >= min_failures:
            results.append({
                'coin_id': ps.coin_id,
                'layer_id': ps.layer_id,
                'consecutive_errors': consecutive,
            })
    return results


def should_skip(coin, step, retry_failed=False):
    """Check if a coin should be skipped for a step.

    Args:
        coin: MigratedCoin instance.
        step: Step config dict.
        retry_failed: If True, bypass consecutive failure check.

    Returns:
        True if the coin should be skipped.
    """
    skip_if = step.get('skip_if')
    if not skip_if:
        return False

    if skip_if == 'pool_mapping_exists':
        return PoolMapping.objects.filter(coin_id=coin.mint_address).exists()

    if skip_if == 'window_complete':
        layer_id = step.get('layer_id')
        if not layer_id:
            return False
        try:
            status = U001PipelineStatus.objects.get(
                coin_id=coin.mint_address,
                layer_id=layer_id,
            )
            if status.status == PipelineCompleteness.WINDOW_COMPLETE:
                return True
        except U001PipelineStatus.DoesNotExist:
            pass
        # Check consecutive failures (unless retrying)
        if not retry_failed:
            max_failures = step.get('max_consecutive_failures', 5)
            if layer_id and has_consecutive_failures(coin, layer_id, max_failures):
                logger.info(
                    "Skipping %s for %s: %d consecutive failures",
                    step['name'], coin.mint_address, max_failures,
                )
                return True
        return False

    if skip_if == 'window_complete_or_immature':
        layer_id = step.get('layer_id')
        if not layer_id:
            return False
        # Skip if already complete
        try:
            status = U001PipelineStatus.objects.get(
                coin_id=coin.mint_address,
                layer_id=layer_id,
            )
            if status.status == PipelineCompleteness.WINDOW_COMPLETE:
                return True
        except U001PipelineStatus.DoesNotExist:
            pass
        # Skip if observation window hasn't closed yet
        if not coin.is_mature:
            return True
        # Check consecutive failures (unless retrying)
        if not retry_failed:
            max_failures = step.get('max_consecutive_failures', 5)
            if layer_id and has_consecutive_failures(coin, layer_id, max_failures):
                logger.info(
                    "Skipping %s for %s: %d consecutive failures",
                    step['name'], coin.mint_address, max_failures,
                )
                return True
        return False

    logger.warning("Unknown skip_if condition: %s", skip_if)
    return False


def call_handler(handler_path, *args, **kwargs):
    """Import and call a handler function by dotted path.

    Args:
        handler_path: e.g. 'pipeline.orchestration.handlers.run_ohlcv'
        *args, **kwargs: passed to the handler.

    Returns:
        Handler return value.
    """
    module_path, func_name = handler_path.rsplit('.', 1)
    module = importlib.import_module(module_path)
    func = getattr(module, func_name)
    return func(*args, **kwargs)


def update_pipeline_status(coin, step, result):
    """Update U001PipelineStatus after a successful step.

    Only applies to steps with a layer_id (feature layer steps).
    Pool mapping has no PipelineStatus entry.
    """
    layer_id = step.get('layer_id')
    if not layer_id:
        return

    # The handler (fetch_ohlcv_for_coin) already updates PipelineStatus
    # internally. This is a safety net for handlers that don't.
    status_val = result.get('status', PipelineCompleteness.PARTIAL)
    U001PipelineStatus.objects.update_or_create(
        coin_id=coin.mint_address,
        layer_id=layer_id,
        defaults={
            'status': status_val,
            'last_run_at': timezone.now(),
            'last_error': None,
        },
    )


def mark_error(coin, step, error_message):
    """Update U001PipelineStatus with error status."""
    layer_id = step.get('layer_id')
    if not layer_id:
        return

    U001PipelineStatus.objects.update_or_create(
        coin_id=coin.mint_address,
        layer_id=layer_id,
        defaults={
            'status': PipelineCompleteness.ERROR,
            'last_run_at': timezone.now(),
            'last_error': error_message,
        },
    )
