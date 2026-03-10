"""Management command to fetch OHLCV data for a token."""

import logging
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand

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
            self.stderr.write(f"MigratedCoin {mint} does not exist")
            return

        # Look up pool address
        try:
            pool = PoolMapping.objects.filter(coin_id=mint).first()
            if not pool:
                raise PoolMapping.DoesNotExist
        except PoolMapping.DoesNotExist:
            self.stderr.write(
                f"No PoolMapping for {mint}. "
                f"Run populate_pool_mapping first."
            )
            return

        # Determine time range
        if options['start'] and options['end']:
            # Re-fill: explicit range
            start = datetime.fromisoformat(options['start'])
            end = datetime.fromisoformat(options['end'])
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            self.stdout.write(f"Re-fill mode: {start} to {end}")
        else:
            # Check watermark
            watermark = get_watermark(mint)
            if coin.anchor_event is None:
                self.stderr.write("Coin has no anchor_event set")
                return

            window_end = (
                coin.anchor_event + MigratedCoin.OBSERVATION_WINDOW_END
            )
            now = datetime.now(timezone.utc)
            end = min(window_end, now)

            if watermark is None:
                # Bootstrap: full observation window
                start = coin.anchor_event
                self.stdout.write(
                    f"Bootstrap mode: {start} to {end}"
                )
            else:
                # Steady-state: windowed incremental with overlap
                start = watermark - OVERLAP
                # Don't go before anchor_event
                if start < coin.anchor_event:
                    start = coin.anchor_event
                self.stdout.write(
                    f"Steady-state mode: {start} to {end} "
                    f"(overlap={OVERLAP})"
                )

        # Connector -> Conformance -> Loader
        self.stdout.write(
            f"Fetching from DexPaprika for pool {pool.pool_address}..."
        )
        raw = fetch_ohlcv(pool.pool_address, start, end)

        if not raw:
            logger.warning(
                "Zero results from API for coin %s (pool %s) in [%s, %s]",
                mint, pool.pool_address, start, end,
            )
            return

        self.stdout.write(f"Received {len(raw)} raw records")

        canonical = conform(raw, mint)
        self.stdout.write(f"Conformed {len(canonical)} records")

        load(mint, start, end, canonical)

        # Reconciliation logging
        resolution_secs = OHLCVCandle.TEMPORAL_RESOLUTION.total_seconds()
        expected_intervals = (end - start).total_seconds() / resolution_secs

        if not canonical:
            logger.warning(
                "All records filtered during conformance for %s", mint,
            )
            return

        timestamps = [r['timestamp'] for r in canonical]
        first_ts = min(timestamps)
        last_ts = max(timestamps)

        self.stdout.write(
            f"Reconciliation: loaded={len(canonical)}, "
            f"theoretical_max={expected_intervals:.0f}, "
            f"first={first_ts}, last={last_ts}"
        )
