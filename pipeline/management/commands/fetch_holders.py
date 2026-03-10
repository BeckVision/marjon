"""Management command to fetch holder snapshots for a token."""

import logging
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError

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
            raise CommandError(f"MigratedCoin {mint} does not exist")

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

        # CU budget guard — abort if insufficient daily budget
        estimated_cu = estimate_cu_cost(start, end)
        daily_used = get_daily_cu_used()
        if daily_used + estimated_cu > DAILY_CU_LIMIT:
            logger.warning(
                "CU budget guard: estimated %d CU for this run, "
                "%d already used today (limit: %d). Aborting.",
                estimated_cu, daily_used, DAILY_CU_LIMIT,
            )
            raise CommandError(
                f"Would exceed daily CU limit. "
                f"Estimated={estimated_cu}, used={daily_used}, "
                f"limit={DAILY_CU_LIMIT}"
            )

        # Connector -> Conformance -> Loader
        logger.info("Fetching from Moralis for %s...", mint)
        try:
            raw = fetch_holders(mint, start, end)
        except Exception:
            logger.error(
                "Connector failed for %s", mint, exc_info=True,
            )
            raise CommandError(f"Moralis connector failed for {mint}")

        if not raw:
            logger.warning(
                "Zero results from API for coin %s in [%s, %s]",
                mint, start, end,
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

        # Reconciliation — stricter for FL-002
        # Moralis returns both boundaries inclusive: +1
        resolution_secs = HolderSnapshot.TEMPORAL_RESOLUTION.total_seconds()
        expected_count = (end - start).total_seconds() / resolution_secs + 1
        loaded_count = len(canonical)

        if loaded_count != int(expected_count):
            logger.warning(
                "Count mismatch for %s: loaded %d but expected %d "
                "(missing intervals)",
                mint, loaded_count, int(expected_count),
            )

        timestamps = [r['timestamp'] for r in canonical]
        first_ts = min(timestamps)
        last_ts = max(timestamps)

        logger.info(
            "Reconciliation for %s: loaded=%d, expected=%d, "
            "first=%s, last=%s",
            mint, loaded_count, int(expected_count), first_ts, last_ts,
        )
