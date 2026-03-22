"""Management command to fetch holder snapshots for a token."""

from django.core.management.base import CommandError

from pipeline.conformance.fl002_moralis import conform  # noqa: F401 — kept for reference
from pipeline.connectors.moralis import (  # noqa: F401 — test mock targets
    DAILY_CU_LIMIT,
    estimate_cu_cost,
    fetch_holders,
    get_daily_cu_used,
)
from pipeline.management.commands._base import BaseFetchCommand
from pipeline.pipelines.fl002 import FL002
from pipeline.runner import run_for_coin


class Command(BaseFetchCommand):
    help = "Fetch holder snapshots from Moralis and load into warehouse"

    def add_arguments(self, parser):
        self.add_common_arguments(parser)

    def handle(self, *args, **options):
        mint = options['coin']
        self.validate_coin(mint)
        start, end = self.parse_time_range(options)

        try:
            result = run_for_coin(FL002, mint, start, end)
        except (ValueError, RuntimeError) as e:
            raise CommandError(str(e))

        self.stdout.write(
            f"Loaded {result['records_loaded']} snapshots "
            f"({result['mode']}, {result['api_calls']} API calls)"
        )
