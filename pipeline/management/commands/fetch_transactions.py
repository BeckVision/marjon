"""Management command to fetch raw transactions for a token.

Supports two sources:
  - shyft: Primary source for recent coins (within 3-4 day retention)
  - helius: Secondary source for historical backfill (full history)
  - auto: Selects based on coin age (default)
"""

from django.core.management.base import CommandError

from pipeline.management.commands._base import BaseFetchCommand
from pipeline.pipelines.rd001 import RD001, SHYFT_RETENTION_DAYS, _select_source  # noqa: F401 — batch imports
from pipeline.runner import run_for_coin


def fetch_transactions_for_coin(mint_address, start=None, end=None,
                                source='auto', parse_workers=1):
    """Core transaction fetch logic for one coin.

    Returns:
        dict with 'status', 'records_loaded', 'records_skipped',
        'api_calls', 'mode', 'run_id', 'error_message'.
    """
    return run_for_coin(
        RD001, mint_address, start, end,
        source=source,
        parse_workers=parse_workers,
    )


class Command(BaseFetchCommand):
    help = "Fetch raw transactions and load into warehouse"

    def add_arguments(self, parser):
        self.add_common_arguments(parser)
        parser.add_argument(
            '--source', type=str, default='auto',
            choices=['shyft', 'helius', 'auto'],
            help='Data source (default: auto — selects by coin age)',
        )
        parser.add_argument(
            '--parse-workers', type=int, default=8,
            help='Concurrent workers for Phase 2 parsing (default: 8)',
        )

    def handle(self, *args, **options):
        mint = options['coin']
        source = options['source']
        self.validate_coin(mint)
        start, end = self.parse_time_range(options)

        try:
            result = fetch_transactions_for_coin(
                mint, start, end, source=source,
                parse_workers=options['parse_workers'],
            )
        except (ValueError, RuntimeError) as e:
            raise CommandError(str(e))

        self.stdout.write(
            f"Loaded {result['records_loaded']} transactions, "
            f"skipped {result['records_skipped']} "
            f"({result['mode']}, {result['api_calls']} API calls, "
            f"source={source})"
        )
