"""Management command to fetch OHLCV data for a token."""

from django.core.management.base import CommandError

from pipeline.conformance.fl001_geckoterminal import conform  # noqa: F401 — test mock target
from pipeline.connectors.geckoterminal import fetch_ohlcv  # noqa: F401 — test mock target
from pipeline.management.commands._base import BaseFetchCommand
from pipeline.pipelines.fl001 import FL001
from pipeline.runner import run_for_coin


def fetch_ohlcv_for_coin(mint_address, start=None, end=None):
    """Core OHLCV fetch logic for one coin.

    Returns:
        dict with 'status', 'records_loaded', 'api_calls', 'mode',
        'run_id', 'error_message'.
    """
    return run_for_coin(FL001, mint_address, start, end)


class Command(BaseFetchCommand):
    help = "Fetch OHLCV candles from GeckoTerminal and load into warehouse"

    def add_arguments(self, parser):
        self.add_common_arguments(parser)

    def handle(self, *args, **options):
        mint = options['coin']
        self.validate_coin(mint)
        self.validate_pool(mint)
        start, end = self.parse_time_range(options)

        try:
            result = fetch_ohlcv_for_coin(mint, start, end)
        except (ValueError, RuntimeError) as e:
            raise CommandError(str(e))

        self.stdout.write(
            f"Loaded {result['records_loaded']} candles "
            f"({result['mode']}, {result['api_calls']} API calls)"
        )
