"""Base class for per-coin fetch commands."""

from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError

from warehouse.models import MigratedCoin, PoolMapping


class BaseFetchCommand(BaseCommand):

    def add_common_arguments(self, parser):
        parser.add_argument('--coin', required=True, help='Mint address')
        parser.add_argument(
            '--start', type=str, default=None,
            help='Start datetime (ISO format)',
        )
        parser.add_argument(
            '--end', type=str, default=None,
            help='End datetime (ISO format)',
        )

    def parse_time_range(self, options):
        """Parse and validate --start/--end. Returns (start, end) or (None, None)."""
        if not options['start'] and not options['end']:
            return None, None
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
        return start, end

    def validate_coin(self, mint):
        try:
            MigratedCoin.objects.get(mint_address=mint)
        except MigratedCoin.DoesNotExist:
            raise CommandError(f"MigratedCoin {mint} does not exist")

    def validate_pool(self, mint):
        if not PoolMapping.objects.filter(coin_id=mint).exists():
            raise CommandError(
                f"No PoolMapping for {mint}. "
                f"Run populate_pool_mapping first."
            )
