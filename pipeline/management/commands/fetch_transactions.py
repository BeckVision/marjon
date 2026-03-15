"""Management command to fetch raw transactions for a token."""

import logging
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from django.utils import timezone as dj_timezone

from pipeline.conformance.rd001_shyft import conform
from pipeline.connectors.shyft import fetch_transactions
from pipeline.loaders.rd001 import get_watermark, load

from warehouse.models import (
    MigratedCoin, PipelineCompleteness, PoolMapping, RawTransaction,
    RunMode, RunStatus, U001PipelineRun, U001PipelineStatus,
)

logger = logging.getLogger(__name__)

# PDP1: 5-minute overlap for incremental runs (from Session B spec)
OVERLAP = timedelta(minutes=5)


def _compute_completeness(coin, mint_address, watermark=None):
    """Determine pipeline completeness for a coin's RD-001 data.

    WINDOW_COMPLETE when either:
      - Watermark reached the window end, OR
      - Coin is mature (window closed).

    PARTIAL otherwise.
    """
    if watermark is None:
        watermark = RawTransaction.objects.filter(
            coin_id=mint_address,
        ).aggregate(Max('timestamp'))['timestamp__max']

    if watermark and coin.window_end_time and watermark >= coin.window_end_time:
        return PipelineCompleteness.WINDOW_COMPLETE

    if coin.is_mature:
        return PipelineCompleteness.WINDOW_COMPLETE

    return PipelineCompleteness.PARTIAL


def fetch_transactions_for_coin(mint_address, start=None, end=None):
    """Core transaction fetch logic for one coin.

    Args:
        mint_address: Token mint address.
        start: Optional start datetime. If omitted, derived from watermark.
        end: Optional end datetime. If omitted, derived from observation window.

    Returns:
        dict with 'status', 'records_loaded', 'records_skipped',
        'api_calls', 'mode', 'run_id', 'error_message'.
    """
    coin = MigratedCoin.objects.get(mint_address=mint_address)

    pool = PoolMapping.objects.filter(
        coin_id=mint_address,
    ).order_by('created_at').first()
    if not pool:
        raise ValueError(
            f"No PoolMapping for {mint_address}. "
            f"Run populate_pool_mapping first."
        )

    # Determine time range and mode
    if start and end:
        mode = RunMode.REFILL
        logger.info("Re-fill mode: %s to %s for %s", start, end, mint_address)
    else:
        if coin.anchor_event is None:
            raise ValueError("Coin has no anchor_event set")

        watermark = get_watermark(mint_address)
        window_end = coin.anchor_event + MigratedCoin.OBSERVATION_WINDOW_END
        now = datetime.now(timezone.utc)
        end = min(window_end, now)

        if watermark is None:
            start = coin.anchor_event
            mode = RunMode.BOOTSTRAP
            logger.info("Bootstrap mode: %s to %s for %s", start, end, mint_address)
        else:
            start = max(watermark - OVERLAP, coin.anchor_event)
            mode = RunMode.STEADY_STATE
            logger.info(
                "Steady-state mode: %s to %s (overlap=%s) for %s",
                start, end, OVERLAP, mint_address,
            )

    # PDP8: Create pipeline run entry
    run = U001PipelineRun.objects.create(
        coin_id=mint_address,
        layer_id=RawTransaction.REFERENCE_ID,
        mode=mode,
        status=RunStatus.STARTED,
        started_at=dj_timezone.now(),
        time_range_start=start,
        time_range_end=end,
    )

    # Pipeline status: mark in-progress
    U001PipelineStatus.objects.update_or_create(
        coin_id=mint_address, layer_id=RawTransaction.REFERENCE_ID,
        defaults={'status': PipelineCompleteness.IN_PROGRESS,
                  'last_run_at': dj_timezone.now()},
    )

    # Connector
    logger.info(
        "Fetching from Shyft for pool %s...", pool.pool_address,
    )
    try:
        raw, meta = fetch_transactions(pool.pool_address, start, end)
    except Exception as e:
        logger.error(
            "Connector failed for %s (pool %s)",
            mint_address, pool.pool_address, exc_info=True,
        )
        run.status = RunStatus.ERROR
        run.completed_at = dj_timezone.now()
        run.error_message = str(e)
        run.save()
        U001PipelineStatus.objects.update_or_create(
            coin_id=mint_address, layer_id=RawTransaction.REFERENCE_ID,
            defaults={'status': PipelineCompleteness.ERROR,
                      'last_run': run,
                      'last_run_at': run.completed_at,
                      'last_error': str(e)},
        )
        raise RuntimeError(f"Shyft connector failed for {mint_address}") from e

    if not raw:
        logger.info(
            "Zero transactions from API for %s (pool %s) in [%s, %s]",
            mint_address, pool.pool_address, start, end,
        )
        run.status = RunStatus.COMPLETE
        run.completed_at = dj_timezone.now()
        run.records_loaded = 0
        run.api_calls = meta['api_calls']
        run.save()
        zero_completeness = _compute_completeness(coin, mint_address)
        U001PipelineStatus.objects.update_or_create(
            coin_id=mint_address, layer_id=RawTransaction.REFERENCE_ID,
            defaults={
                'status': zero_completeness,
                'last_run': run,
                'last_run_at': run.completed_at,
                'last_error': None,
            },
        )
        return {
            'status': zero_completeness, 'records_loaded': 0,
            'records_skipped': 0, 'api_calls': meta['api_calls'],
            'mode': mode, 'run_id': run.id, 'error_message': None,
        }

    logger.info("Received %d raw transactions for %s", len(raw), mint_address)

    # Conformance (returns two lists)
    try:
        parsed, skipped = conform(raw, mint_address, pool.pool_address)
    except Exception as e:
        logger.error(
            "Conformance failed for %s (%d raw transactions)",
            mint_address, len(raw), exc_info=True,
        )
        run.status = RunStatus.ERROR
        run.completed_at = dj_timezone.now()
        run.error_message = str(e)
        run.api_calls = meta['api_calls']
        run.save()
        U001PipelineStatus.objects.update_or_create(
            coin_id=mint_address, layer_id=RawTransaction.REFERENCE_ID,
            defaults={'status': PipelineCompleteness.ERROR,
                      'last_run': run,
                      'last_run_at': run.completed_at,
                      'last_error': str(e)},
        )
        raise RuntimeError(f"Conformance failed for {mint_address}") from e

    # Loader (writes both parsed + skipped)
    load(mint_address, start, end, parsed, skipped)

    # Reconciliation logging (informational — trade count varies)
    logger.info(
        "Reconciliation for %s: raw=%d, parsed=%d, skipped=%d, "
        "api_calls=%d",
        mint_address, len(raw), len(parsed), len(skipped),
        meta['api_calls'],
    )

    # PDP8: Update pipeline run on success
    run.status = RunStatus.COMPLETE
    run.completed_at = dj_timezone.now()
    run.records_loaded = len(parsed)
    run.api_calls = meta['api_calls']
    run.save()

    # Pipeline status: compute watermark and completeness
    new_watermark = RawTransaction.objects.filter(
        coin_id=mint_address,
    ).aggregate(Max('timestamp'))['timestamp__max']

    completeness = _compute_completeness(coin, mint_address, new_watermark)

    U001PipelineStatus.objects.update_or_create(
        coin_id=mint_address, layer_id=RawTransaction.REFERENCE_ID,
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
        'records_loaded': len(parsed),
        'records_skipped': len(skipped),
        'api_calls': meta['api_calls'],
        'mode': mode,
        'run_id': run.id,
        'error_message': None,
    }


class Command(BaseCommand):
    help = "Fetch raw transactions from Shyft and load into warehouse"

    def add_arguments(self, parser):
        parser.add_argument('--coin', required=True, help='Mint address')
        parser.add_argument(
            '--start', type=str, default=None,
            help='Start datetime (ISO format)',
        )
        parser.add_argument(
            '--end', type=str, default=None,
            help='End datetime (ISO format)',
        )

    def handle(self, *args, **options):
        mint = options['coin']

        try:
            MigratedCoin.objects.get(mint_address=mint)
        except MigratedCoin.DoesNotExist:
            raise CommandError(f"MigratedCoin {mint} does not exist")

        # Parse optional start/end
        start = None
        end = None
        if options['start'] or options['end']:
            if not (options['start'] and options['end']):
                raise CommandError(
                    "--start and --end must both be provided for re-fill mode"
                )
            try:
                start = datetime.fromisoformat(options['start'])
                end = datetime.fromisoformat(options['end'])
            except ValueError as e:
                raise CommandError(f"Invalid date format: {e}")
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if start >= end:
                raise CommandError(
                    f"--start ({start}) must be before --end ({end})"
                )

        try:
            result = fetch_transactions_for_coin(mint, start, end)
        except (ValueError, RuntimeError) as e:
            raise CommandError(str(e))

        self.stdout.write(
            f"Loaded {result['records_loaded']} transactions, "
            f"skipped {result['records_skipped']} "
            f"({result['mode']}, {result['api_calls']} API calls)"
        )
