"""Bulk CSV backfill for U-002 feature layers.

Downloads and loads historical data from data.binance.vision CSVs.

Usage:
    python manage.py backfill_u002 --layer klines --start 2024-03-01 --end 2026-03-01
    python manage.py backfill_u002 --layer klines --start 2024-03-01 --end 2026-03-01 --symbols BTCUSDT
    python manage.py backfill_u002 --layer metrics --start 2024-03-01 --end 2026-03-01
    python manage.py backfill_u002 --layer funding --start 2024-03-01 --end 2026-03-01
"""

import logging
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError

from pipeline.runner import run_for_coin
from warehouse.models import BinanceAsset

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Bulk CSV backfill for U-002 feature layers"

    def add_arguments(self, parser):
        parser.add_argument(
            '--layer', required=True,
            choices=['klines', 'metrics', 'funding'],
            help='Which layer to backfill',
        )
        parser.add_argument(
            '--start', required=True,
            help='Start date (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--end', required=True,
            help='End date (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--symbols', type=str, default=None,
            help='Comma-separated symbols (default: all U-002 assets)',
        )

    def handle(self, *args, **options):
        start = datetime.strptime(options['start'], '%Y-%m-%d').replace(
            tzinfo=timezone.utc,
        )
        end = datetime.strptime(options['end'], '%Y-%m-%d').replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc,
        )

        if options['symbols']:
            symbols = options['symbols'].split(',')
        else:
            symbols = list(
                BinanceAsset.objects.values_list('symbol', flat=True)
            )
            if not symbols:
                raise CommandError(
                    "No U-002 assets found. Run seed_u002 first."
                )

        layer = options['layer']
        if layer == 'klines':
            from pipeline.pipelines.u002_fl001 import U002_FL001
            spec = U002_FL001
        elif layer == 'metrics':
            from pipeline.pipelines.u002_fl003 import U002_FL003
            spec = U002_FL003
        elif layer == 'funding':
            from pipeline.pipelines.u002_fl004 import U002_FL004
            spec = U002_FL004

        self.stdout.write(
            f"Backfilling {layer} for {len(symbols)} symbols: "
            f"{', '.join(symbols)}"
        )
        self.stdout.write(f"Range: {start.date()} to {end.date()}")

        succeeded = 0
        failed = 0

        for symbol in symbols:
            self.stdout.write(f"\n--- {symbol} ---")
            try:
                result = run_for_coin(
                    spec, symbol, start=start, end=end, source='csv',
                )
                records = result.get('records_loaded', 0)
                self.stdout.write(
                    f"  {symbol}: {records} records loaded"
                )
                succeeded += 1
            except Exception as e:
                logger.error(
                    "Backfill failed for %s: %s", symbol, e, exc_info=True,
                )
                self.stderr.write(f"  {symbol}: FAILED — {e}")
                failed += 1

        self.stdout.write(
            f"\nDone: {succeeded} succeeded, {failed} failed"
        )
