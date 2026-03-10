"""Management command to fetch OHLCV data for a token."""

import logging
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError

from pipeline.conformance.fl001_dexpaprika import conform
from pipeline.connectors.dexpaprika import fetch_ohlcv
from pipeline.loaders.fl001 import get_watermark, load
from warehouse.models import MigratedCoin, OHLCVCandle, PoolMapping

logger = logging.getLogger(__name__)

# PDP1: windowed incremental overlap — safety margin for watermark edge cases
OVERLAP = timedelta(minutes=30)


class Command(BaseCommand):
    help = "Fetch OHLCV candles from DexPaprika and load into warehouse"

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

        # Validate coin exists
        try:
            coin = MigratedCoin.objects.get(mint_address=mint)
        except MigratedCoin.DoesNotExist:
            raise CommandError(f"MigratedCoin {mint} does not exist")

        # Look up pool address (earliest created = graduation pool)
        pool = PoolMapping.objects.filter(
            coin_id=mint,
        ).order_by('created_at').first()
        if not pool:
            raise CommandError(
                f"No PoolMapping for {mint}. "
                f"Run populate_pool_mapping first."
            )

        # Determine time range
        if options['start'] or options['end']:
            # Re-fill: both must be provided
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
            logger.info("Re-fill mode: %s to %s for %s", start, end, mint)
        else:
            if coin.anchor_event is None:
                raise CommandError("Coin has no anchor_event set")

            watermark = get_watermark(mint)
            window_end = (
                coin.anchor_event + MigratedCoin.OBSERVATION_WINDOW_END
            )
            now = datetime.now(timezone.utc)
            end = min(window_end, now)

            if watermark is None:
                # Bootstrap: full observation window
                start = coin.anchor_event
                logger.info(
                    "Bootstrap mode: %s to %s for %s", start, end, mint,
                )
            else:
                # Steady-state: windowed incremental with overlap
                start = max(watermark - OVERLAP, coin.anchor_event)
                logger.info(
                    "Steady-state mode: %s to %s (overlap=%s) for %s",
                    start, end, OVERLAP, mint,
                )

        # Connector -> Conformance -> Loader
        logger.info(
            "Fetching from DexPaprika for pool %s...", pool.pool_address,
        )
        try:
            raw = fetch_ohlcv(pool.pool_address, start, end)
        except Exception:
            logger.error(
                "Connector failed for %s (pool %s)",
                mint, pool.pool_address, exc_info=True,
            )
            raise CommandError(f"DexPaprika connector failed for {mint}")

        if not raw:
            logger.warning(
                "Zero results from API for coin %s (pool %s) in [%s, %s]",
                mint, pool.pool_address, start, end,
            )
            return

        logger.info("Received %d raw records for %s", len(raw), mint)

        try:
            canonical = conform(raw, mint)
        except Exception:
            logger.error(
                "Conformance failed for %s (%d raw records)",
                mint, len(raw), exc_info=True,
            )
            raise CommandError(f"Conformance failed for {mint}")

        if not canonical:
            logger.warning(
                "All %d records filtered during conformance for %s",
                len(raw), mint,
            )
            return

        load(mint, start, end, canonical)

        # Reconciliation logging
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
