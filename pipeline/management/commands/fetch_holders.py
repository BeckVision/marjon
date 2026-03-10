"""Management command to fetch holder snapshots for a token."""

import logging
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand

from pipeline.conformance.fl002_moralis import conform
from pipeline.connectors.moralis import (
    DAILY_CU_LIMIT,
    estimate_cu_cost,
    fetch_holders,
    get_daily_cu_used,
)
from pipeline.loaders.fl002 import get_watermark, load
from warehouse.models import HolderSnapshot, MigratedCoin

logger = logging.getLogger(__name__)

# PDP1: windowed incremental overlap — safety margin for watermark edge cases
OVERLAP = timedelta(minutes=30)


class Command(BaseCommand):
    help = "Fetch holder snapshots from Moralis and load into warehouse"

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
            coin = MigratedCoin.objects.get(mint_address=mint)
        except MigratedCoin.DoesNotExist:
            self.stderr.write(f"MigratedCoin {mint} does not exist")
            return

        # Determine time range
        if options['start'] and options['end']:
            start = datetime.fromisoformat(options['start'])
            end = datetime.fromisoformat(options['end'])
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            self.stdout.write(f"Re-fill mode: {start} to {end}")
        else:
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
                start = coin.anchor_event
                self.stdout.write(f"Bootstrap mode: {start} to {end}")
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

        # CU budget guard — abort if insufficient daily budget
        estimated_cu = estimate_cu_cost(start, end)
        daily_used = get_daily_cu_used()
        if daily_used + estimated_cu > DAILY_CU_LIMIT:
            logger.warning(
                "CU budget guard: estimated %d CU for this run, "
                "%d already used today (limit: %d). Aborting.",
                estimated_cu, daily_used, DAILY_CU_LIMIT,
            )
            self.stderr.write(
                f"ABORTED: would exceed daily CU limit. "
                f"Estimated={estimated_cu}, used={daily_used}, "
                f"limit={DAILY_CU_LIMIT}"
            )
            return

        # Connector -> Conformance -> Loader
        self.stdout.write(f"Fetching from Moralis for {mint}...")
        raw = fetch_holders(mint, start, end)

        if not raw:
            logger.warning(
                "Zero results from API for coin %s in [%s, %s]",
                mint, start, end,
            )
            return

        self.stdout.write(f"Received {len(raw)} raw records")

        canonical = conform(raw, mint)
        self.stdout.write(f"Conformed {len(canonical)} records")

        load(mint, start, end, canonical)

        # Reconciliation — stricter for FL-002
        # Moralis returns both boundaries inclusive: +1
        resolution_secs = HolderSnapshot.TEMPORAL_RESOLUTION.total_seconds()
        expected_count = (
            (end - start).total_seconds() / resolution_secs + 1
        )
        loaded_count = len(canonical)

        if loaded_count != int(expected_count):
            logger.warning(
                "Count mismatch for %s: loaded %d but expected %d "
                "(missing intervals)",
                mint, loaded_count, int(expected_count),
            )

        if not canonical:
            logger.warning(
                "All records filtered during conformance for %s", mint,
            )
            return

        timestamps = [r['timestamp'] for r in canonical]
        first_ts = min(timestamps)
        last_ts = max(timestamps)

        self.stdout.write(
            f"Reconciliation: loaded={loaded_count}, "
            f"expected={int(expected_count)}, "
            f"first={first_ts}, last={last_ts}"
        )
