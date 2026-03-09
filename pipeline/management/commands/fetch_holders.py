"""Management command to fetch holder snapshots for a token."""

import logging
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand

from pipeline.conformance.fl002_moralis import conform
from pipeline.connectors.moralis import fetch_holders
from pipeline.loaders.fl002 import get_watermark, load
from warehouse.models import MigratedCoin

logger = logging.getLogger(__name__)


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
                start = watermark
                self.stdout.write(
                    f"Steady-state mode: {start} to {end}"
                )

        # Connector -> Conformance -> Loader
        self.stdout.write(f"Fetching from Moralis for {mint}...")
        raw = fetch_holders(mint, start, end)

        if not raw:
            self.stdout.write("No data returned from API")
            return

        self.stdout.write(f"Received {len(raw)} raw records")

        canonical = conform(raw, mint)
        self.stdout.write(f"Conformed {len(canonical)} records")

        load(mint, start, end, canonical)

        # Reconciliation — stricter for FL-002
        expected_count = (end - start).total_seconds() / 300
        loaded_count = len(canonical)

        if loaded_count != int(expected_count):
            self.stderr.write(
                f"WARNING: loaded {loaded_count} but expected "
                f"{expected_count:.0f} (missing intervals)"
            )

        timestamps = [r['timestamp'] for r in canonical]
        first_ts = min(timestamps)
        last_ts = max(timestamps)

        self.stdout.write(
            f"Reconciliation: loaded={loaded_count}, "
            f"expected={expected_count:.0f}, "
            f"first={first_ts}, last={last_ts}"
        )
