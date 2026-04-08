"""Batch fetch raw transactions for all active coins.

Designed for hourly cron execution. Uses batch RPC to discover new
signatures across the entire universe in one pass, then parses and
loads in parallel.

All parameters are configurable building blocks — tweak for your
hardware, API tier, and urgency.

Usage:
  # Default hourly run (auto source, conservative free-tier defaults)
  python manage.py fetch_transactions_batch

  # Conservative (fewer workers, lower rate)
  python manage.py fetch_transactions_batch --workers 2 --rpc-batch-size 100

  # Aggressive (max throughput)
  python manage.py fetch_transactions_batch --workers 4 --rpc-batch-size 250

  # Dry run (discover only, show what would be processed)
  python manage.py fetch_transactions_batch --dry-run

  # Limit scope
  python manage.py fetch_transactions_batch --max-coins 50 --source shyft

  # Force Helius for everything (historical backfill mode)
  python manage.py fetch_transactions_batch --source helius --max-coins 100
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand
from django.utils import timezone as dj_timezone

from pipeline.connectors.shyft import discover_new_signatures
from pipeline.management.commands.fetch_transactions import (
    SHYFT_RETENTION_DAYS,
    _select_source,
    fetch_transactions_for_coin,
)
from warehouse.models import (
    MigratedCoin, PipelineCompleteness, PoolMapping,
    U001PipelineStatus,
)

logger = logging.getLogger(__name__)


def _env_int(name, default):
    value = os.environ.get(name)
    return int(value) if value else default


def _env_float(name, default):
    value = os.environ.get(name)
    return float(value) if value else default


DEFAULT_WORKERS = _env_int('MARJON_U001_RD001_BATCH_WORKERS', 1)
DEFAULT_RPC_BATCH_SIZE = min(_env_int('MARJON_U001_RD001_RPC_BATCH_SIZE', 100), 250)
DEFAULT_MIN_SIGS = _env_int('MARJON_U001_RD001_MIN_SIGS', 3)
DEFAULT_SLEEP = _env_float('MARJON_U001_RD001_SLEEP', 1.0)
DEFAULT_PARSE_WORKERS = _env_int('MARJON_U001_RD001_PARSE_WORKERS', 1)
DEFAULT_MAX_NEW_SIGS = _env_int('MARJON_U001_RD001_MAX_NEW_SIGS', 500)


def _min_utc_datetime():
    return datetime.min.replace(tzinfo=timezone.utc)


FREE_TIER_GUARD_TEXT = 'exceeds free-tier guard'


def _get_active_coins(
    source='auto',
    status_filter='incomplete',
    include_free_tier_guarded=False,
):
    """Return coins eligible for batch processing.

    For shyft/auto: only coins within Shyft's retention window (recent).
    For helius: coins beyond retention that aren't yet complete.
    """
    now = datetime.now(timezone.utc)

    # Start with coins that have pool mappings and aren't complete
    active_mints = set(
        PoolMapping.objects.values_list('coin_id', flat=True)
    )
    complete_mints = set(
        U001PipelineStatus.objects
        .filter(
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        .values_list('coin_id', flat=True)
    )
    pending = active_mints - complete_mints

    if status_filter != 'incomplete':
        status_qs = U001PipelineStatus.objects.filter(
            layer_id='RD-001',
            status=status_filter,
        )
        if (
            not include_free_tier_guarded
            and status_filter in {'error', 'partial'}
        ):
            status_qs = status_qs.exclude(
                last_error__icontains=FREE_TIER_GUARD_TEXT,
            )
        filtered_mints = set(status_qs.values_list('coin_id', flat=True))
        pending &= filtered_mints

    qs = MigratedCoin.objects.filter(mint_address__in=pending)

    # Filter by age based on source
    if source in ('shyft', 'auto'):
        # Only recent coins that Shyft can serve
        cutoff = now - timedelta(days=SHYFT_RETENTION_DAYS)
        qs = qs.filter(anchor_event__gte=cutoff)
    elif source == 'helius':
        # Only old coins beyond Shyft's retention
        cutoff = now - timedelta(days=SHYFT_RETENTION_DAYS)
        qs = qs.filter(anchor_event__lt=cutoff)

    return list(qs)


def _order_work_queue(mint_sig_counts, status_last_run, status_filter):
    """Order queue according to the goal of the run."""
    if status_filter in {'error', 'partial'}:
        return sorted(
            mint_sig_counts.keys(),
            key=lambda mint: (
                status_last_run.get(mint) or _min_utc_datetime(),
                mint_sig_counts[mint],
                mint,
            ),
        )

    return sorted(
        mint_sig_counts.keys(),
        key=lambda mint: mint_sig_counts[mint],
        reverse=True,
    )


def _order_status_only_queue(work_queue, status_last_run):
    """Order a queue that has no signature-count metadata yet."""
    return sorted(
        work_queue,
        key=lambda mint: (status_last_run.get(mint) or _min_utc_datetime(), mint),
    )


def _build_pool_watermarks(coins):
    """Build {pool_address: last_signature} map for batch RPC discovery.

    Returns:
        Tuple of (pool_watermarks, pool_to_mint) where:
        - pool_watermarks: {pool_address: last_sig_or_None}
        - pool_to_mint: {pool_address: mint_address}
    """
    from django.db.models import Subquery, OuterRef
    from warehouse.models import RawTransaction

    mint_ids = [c.mint_address for c in coins]

    pool_mappings = PoolMapping.objects.filter(
        coin_id__in=mint_ids,
    ).select_related('coin')

    # Single query: annotate each pool mapping with its coin's latest tx_signature
    latest_sig_subquery = Subquery(
        RawTransaction.objects
        .filter(coin_id=OuterRef('coin_id'))
        .order_by('-timestamp')
        .values('tx_signature')[:1]
    )
    annotated = pool_mappings.annotate(last_sig=latest_sig_subquery)

    pool_watermarks = {}
    pool_to_mint = {}
    for pm in annotated:
        pool_watermarks[pm.pool_address] = pm.last_sig
        pool_to_mint[pm.pool_address] = pm.coin_id

    return pool_watermarks, pool_to_mint


def _process_coin(mint_address, source, parse_workers=1):
    """Process one coin. Called from thread pool."""
    try:
        result = fetch_transactions_for_coin(
            mint_address, source=source, parse_workers=parse_workers,
        )
        return mint_address, result, None
    except Exception as e:
        return mint_address, None, str(e)


class Command(BaseCommand):
    help = "Batch fetch raw transactions for all active coins (hourly cron)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--workers', type=int, default=DEFAULT_WORKERS,
            help=f'Concurrent coin workers (default: {DEFAULT_WORKERS})',
        )
        parser.add_argument(
            '--rpc-batch-size', type=int, default=DEFAULT_RPC_BATCH_SIZE,
            help=f'Pools per batch RPC request (default: {DEFAULT_RPC_BATCH_SIZE}, max 250)',
        )
        parser.add_argument(
            '--max-coins', type=int, default=0,
            help='Max coins to process (0 = all, default: 0)',
        )
        parser.add_argument(
            '--source', type=str, default='auto',
            choices=['shyft', 'helius', 'auto'],
            help='Data source (default: auto)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Discover signatures only, do not parse or load',
        )
        parser.add_argument(
            '--min-sigs', type=int, default=DEFAULT_MIN_SIGS,
            help=f'Skip coins with fewer new signatures (default: {DEFAULT_MIN_SIGS})',
        )
        parser.add_argument(
            '--sleep', type=float, default=DEFAULT_SLEEP,
            help=f'Seconds between coins in sequential mode (default: {DEFAULT_SLEEP})',
        )
        parser.add_argument(
            '--parse-workers', type=int, default=DEFAULT_PARSE_WORKERS,
            help=f'Concurrent workers for Phase 2 parsing per coin (default: {DEFAULT_PARSE_WORKERS})',
        )
        parser.add_argument(
            '--max-new-sigs', type=int, default=DEFAULT_MAX_NEW_SIGS,
            help=f'Skip coins with more than this many newly discovered signatures (default: {DEFAULT_MAX_NEW_SIGS})',
        )
        parser.add_argument(
            '--status-filter', type=str, default='incomplete',
            choices=['incomplete', 'error', 'partial'],
            help='Limit the queue to RD-001 statuses in this bucket (default: incomplete)',
        )
        parser.add_argument(
            '--include-free-tier-guarded', action='store_true',
            help='Include partial/error rows whose last error exceeded the free-tier guard',
        )

    def handle(self, *args, **options):
        workers = options['workers']
        rpc_batch_size = min(options['rpc_batch_size'], 250)
        max_coins = options['max_coins']
        source = options['source']
        dry_run = options['dry_run']
        min_sigs = options['min_sigs']
        sleep_between = options['sleep']

        parse_workers = options['parse_workers']
        max_new_sigs = options['max_new_sigs']
        status_filter = options['status_filter']
        include_free_tier_guarded = options['include_free_tier_guarded']

        started = dj_timezone.now()
        self.stdout.write(f"Batch run started at {started}")
        self.stdout.write(
            f"Config: workers={workers}, parse_workers={parse_workers}, "
            f"rpc_batch={rpc_batch_size}, "
            f"source={source}, max_coins={max_coins or 'all'}, "
            f"min_sigs={min_sigs}, status_filter={status_filter}, dry_run={dry_run}"
        )

        # Step 1: Find active coins
        coins = _get_active_coins(
            source=source,
            status_filter=status_filter,
            include_free_tier_guarded=include_free_tier_guarded,
        )
        self.stdout.write(
            f"Active coins for source={source}, status_filter={status_filter}: {len(coins)}"
        )
        if (
            status_filter in {'error', 'partial'}
            and not include_free_tier_guarded
        ):
            guarded_total = U001PipelineStatus.objects.filter(
                layer_id='RD-001',
                status=status_filter,
                last_error__icontains=FREE_TIER_GUARD_TEXT,
            ).count()
            if guarded_total:
                self.stdout.write(
                    f"Skipping {guarded_total} {status_filter} coins marked by the free-tier guard"
                )

        if not coins:
            self.stdout.write("Nothing to process.")
            return

        status_last_run = {
            row['coin_id']: row['last_run_at']
            for row in U001PipelineStatus.objects.filter(
                layer_id='RD-001',
                coin_id__in=[c.mint_address for c in coins],
            ).values('coin_id', 'last_run_at')
        }

        # Step 2: Discover which coins need processing
        if source == 'helius':
            # Helius mode: skip Shyft batch RPC discovery entirely.
            # Just queue all coins — each fetch_transactions_for_coin
            # handles its own discovery via Helius RPC.
            self.stdout.write(
                "Phase 1: Skipped (Helius handles discovery per coin)"
            )
            work_queue = [c.mint_address for c in coins]
        else:
            # Shyft/auto mode: batch RPC discovery for efficiency
            self.stdout.write("Phase 1: Batch signature discovery...")
            pool_watermarks, pool_to_mint = _build_pool_watermarks(coins)
            self.stdout.write(f"  Pools to check: {len(pool_watermarks)}")

            new_sigs = discover_new_signatures(pool_watermarks)

            # Map back to mints and count
            mint_sig_counts = {}
            for pool, sigs in new_sigs.items():
                mint = pool_to_mint.get(pool)
                if mint and len(sigs) >= min_sigs:
                    mint_sig_counts[mint] = len(sigs)

            total_new_sigs = sum(mint_sig_counts.values())
            self.stdout.write(
                f"  Discovered {total_new_sigs} new signatures "
                f"across {len(mint_sig_counts)} coins"
            )

            oversized = {
                mint: count for mint, count in mint_sig_counts.items()
                if count > max_new_sigs
            }
            if oversized:
                self.stdout.write(
                    f"  Skipping {len(oversized)} oversized coins "
                    f"(>{max_new_sigs} new signatures)"
                )
                mint_sig_counts = {
                    mint: count for mint, count in mint_sig_counts.items()
                    if count <= max_new_sigs
                }

            if not mint_sig_counts:
                self.stdout.write("No new signatures found. Nothing to process.")
                return

            work_queue = _order_work_queue(
                mint_sig_counts, status_last_run, status_filter,
            )

        if source == 'helius' and status_filter in {'error', 'partial'}:
            work_queue = _order_status_only_queue(work_queue, status_last_run)

        if max_coins:
            work_queue = work_queue[:max_coins]

        self.stdout.write(f"  Processing {len(work_queue)} coins")

        # Pre-fetch all coins to avoid N+1 queries in processing loops
        coins_by_mint = {
            c.mint_address: c
            for c in MigratedCoin.objects.filter(mint_address__in=work_queue)
        }

        # Pre-resolve source per coin (one pass, not per-iteration)
        def _resolve_source(mint):
            if source != 'auto':
                return source
            coin = coins_by_mint.get(mint)
            return _select_source(coin) if coin else source

        if dry_run:
            self.stdout.write("\n--- DRY RUN: would process ---")
            for mint in work_queue[:20]:
                coin_source = _resolve_source(mint)
                self.stdout.write(
                    f"  {mint[:30]}... source={coin_source}"
                )
            if len(work_queue) > 20:
                self.stdout.write(f"  ... and {len(work_queue) - 20} more")
            return

        # Step 3: Parse + conform + load (concurrent)
        self.stdout.write(
            f"\nPhase 2: Processing {len(work_queue)} coins "
            f"with {workers} workers..."
        )

        succeeded = 0
        failed = 0
        total_loaded = 0
        total_skipped = 0
        total_calls = 0

        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {}
                for mint in work_queue:
                    coin_source = _resolve_source(mint)
                    future = executor.submit(
                        _process_coin, mint, coin_source, parse_workers,
                    )
                    futures[future] = mint

                for future in as_completed(futures):
                    mint = futures[future]
                    mint_short = mint[:20]
                    try:
                        _, result, error = future.result()
                        if error:
                            failed += 1
                            logger.error(
                                "batch: %s... FAILED: %s", mint_short, error,
                            )
                        else:
                            succeeded += 1
                            loaded = result.get('records_loaded', 0)
                            skipped_r = result.get('records_skipped', 0)
                            calls = result.get('api_calls', 0)
                            total_loaded += loaded
                            total_skipped += skipped_r
                            total_calls += calls
                            logger.info(
                                "batch: %s... %d loaded (%d calls)",
                                mint_short, loaded, calls,
                            )
                    except Exception as e:
                        failed += 1
                        logger.error(
                            "batch: %s... EXCEPTION: %s", mint_short, e,
                        )
        else:
            # Sequential mode
            for mint in work_queue:
                coin_source = _resolve_source(mint)
                mint_short = mint[:20]

                _, result, error = _process_coin(mint, coin_source, parse_workers)
                if error:
                    failed += 1
                    logger.error(
                        "batch: %s... FAILED: %s", mint_short, error,
                    )
                else:
                    succeeded += 1
                    loaded = result.get('records_loaded', 0)
                    total_loaded += loaded
                    total_skipped += result.get('records_skipped', 0)
                    total_calls += result.get('api_calls', 0)
                    logger.info(
                        "batch: %s... %d loaded (%d calls)",
                        mint_short, loaded, result.get('api_calls', 0),
                    )

                time.sleep(sleep_between)

        elapsed = (dj_timezone.now() - started).total_seconds()
        self.stdout.write(
            f"\nBatch complete in {elapsed:.0f}s: "
            f"{succeeded} succeeded, {failed} failed, "
            f"{total_loaded} loaded, {total_skipped} skipped, "
            f"{total_calls} API calls"
        )
