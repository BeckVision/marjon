"""Continuous order book polling for U-002 FL-002.

Captures one snapshot per symbol per minute. Runs indefinitely until
interrupted (Ctrl+C or kill). Designed to run as a background process
or systemd service.

Usage:
    python manage.py poll_orderbook
    python manage.py poll_orderbook --interval 60 --symbols BTCUSDT,ETHUSDT
    python manage.py poll_orderbook --once    # Single snapshot, then exit
"""

import logging
import time
from datetime import datetime, timezone

from django.core.management.base import BaseCommand

from pipeline.conformance.u002_fl002_binance import conform
from pipeline.connectors.binance_orderbook import fetch_order_book
from pipeline.loaders.u002_fl002 import load
from warehouse.models import BinanceAsset

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
DEFAULT_INTERVAL = 60  # seconds


class Command(BaseCommand):
    help = "Poll Binance order book snapshots for U-002 assets (runs continuously)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval', type=int, default=DEFAULT_INTERVAL,
            help=f'Polling interval in seconds (default: {DEFAULT_INTERVAL})',
        )
        parser.add_argument(
            '--symbols', type=str, default=None,
            help='Comma-separated symbols (default: all U-002 assets)',
        )
        parser.add_argument(
            '--once', action='store_true',
            help='Capture one snapshot and exit (for testing)',
        )
        parser.add_argument(
            '--depth', type=int, default=20,
            help='Number of order book levels per side (default: 20)',
        )

    def handle(self, *args, **options):
        interval = options['interval']
        once = options['once']
        depth = options['depth']

        if options['symbols']:
            symbols = options['symbols'].split(',')
        else:
            symbols = list(
                BinanceAsset.objects.values_list('symbol', flat=True)
            )
            if not symbols:
                symbols = DEFAULT_SYMBOLS

        self.stdout.write(
            f"Polling order book for {', '.join(symbols)} "
            f"every {interval}s (depth={depth})"
        )

        snapshot_count = 0
        try:
            while True:
                cycle_start = time.time()

                for symbol in symbols:
                    try:
                        raw, meta = fetch_order_book(symbol, depth=depth)
                        canonical = conform(
                            raw, symbol,
                            capture_time=meta['capture_time'],
                        )
                        load(symbol, canonical)
                        snapshot_count += 1
                    except Exception as e:
                        logger.error(
                            "Order book poll failed for %s: %s",
                            symbol, e, exc_info=True,
                        )

                if once:
                    self.stdout.write(
                        f"Captured {len(symbols)} snapshots "
                        f"({len(symbols) * depth * 2} rows)"
                    )
                    break

                # Sleep for remaining interval time
                elapsed = time.time() - cycle_start
                sleep_time = max(0, interval - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

                if snapshot_count % (len(symbols) * 10) == 0:
                    self.stdout.write(
                        f"Snapshots captured: {snapshot_count} "
                        f"({datetime.now(timezone.utc):%H:%M:%S})"
                    )

        except KeyboardInterrupt:
            self.stdout.write(
                f"\nStopped. Total snapshots: {snapshot_count}"
            )
