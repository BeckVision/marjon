"""Batch fetch raw transactions for all active coins.

Designed for hourly cron execution. Uses batch RPC to discover new
signatures across the entire universe in one pass, then parses and
loads in parallel.

All parameters are configurable building blocks — tweak for your
hardware, API tier, and urgency.

Usage:
  # Default hourly run (auto source, 4 workers)
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


def _get_active_coins(source='auto'):
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
            '--workers', type=int, default=4,
            help='Concurrent parse workers (default: 4)',
        )
        parser.add_argument(
            '--rpc-batch-size', type=int, default=250,
            help='Pools per batch RPC request (default: 250, max 250)',
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
            '--min-sigs', type=int, default=1,
            help='Skip coins with fewer new signatures (default: 1)',
        )
        parser.add_argument(
            '--sleep', type=float, default=0.5,
            help='Seconds between coins in sequential mode (default: 0.5)',
        )
        parser.add_argument(
            '--parse-workers', type=int, default=8,
            help='Concurrent workers for Phase 2 parsing per coin (default: 8)',
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

        started = dj_timezone.now()
        self.stdout.write(f"Batch run started at {started}")
        self.stdout.write(
            f"Config: workers={workers}, parse_workers={parse_workers}, "
            f"rpc_batch={rpc_batch_size}, "
            f"source={source}, max_coins={max_coins or 'all'}, "
            f"min_sigs={min_sigs}, dry_run={dry_run}"
        )

        # Step 1: Find active coins
        coins = _get_active_coins(source=source)
        self.stdout.write(f"Active coins for source={source}: {len(coins)}")

        if not coins:
            self.stdout.write("Nothing to process.")
            return

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

            if not mint_sig_counts:
                self.stdout.write("No new signatures found. Nothing to process.")
                return

            # Sort by sig count descending (process busiest coins first)
            work_queue = sorted(
                mint_sig_counts.keys(),
                key=lambda m: mint_sig_counts[m],
                reverse=True,
            )

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
