"""Management command: pipeline orchestrator.

Chains all pipeline steps together for a universe, runs them in dependency
order, processes coins in batches, tracks progress, and supports resume.

Usage:
    python manage.py orchestrate --universe u001 --days 7
    python manage.py orchestrate --universe u001 --steps discovery,ohlcv
    python manage.py orchestrate --universe u001 --resume
    python manage.py orchestrate --universe u001 --coins 5 --dry-run
"""

import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pipeline.orchestration.utils import (
    call_handler,
    get_coins_to_process,
    load_universe_config,
    mark_error,
    resolve_step_order,
    should_skip,
    update_pipeline_status,
)
from warehouse.models import (
    MigratedCoin, PipelineBatchRun, RunMode, RunStatus,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the full pipeline for a universe: discovery → pool mapping → feature layers"

    def add_arguments(self, parser):
        parser.add_argument(
            '--universe', required=True,
            help='Universe ID (e.g. u001). Maps to pipeline/universes/{id}.py',
        )
        parser.add_argument(
            '--days', type=int, default=None,
            help='Only process coins graduated in the last N days',
        )
        parser.add_argument(
            '--steps', type=str, default=None,
            help='Comma-separated list of steps to run (default: all)',
        )
        parser.add_argument(
            '--resume', action='store_true',
            help='Skip coins already at window_complete (default behavior — for clarity)',
        )
        parser.add_argument(
            '--coins', type=int, default=None,
            help='Max number of coins to process per step',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be executed without doing it',
        )

    def handle(self, *args, **options):
        # 1. Load universe config
        try:
            config = load_universe_config(options['universe'])
        except ValueError as e:
            raise CommandError(str(e))

        # Parse requested steps
        requested_steps = None
        run_discovery = True
        if options['steps']:
            requested_steps = set(options['steps'].split(','))
            run_discovery = 'discovery' in requested_steps
            requested_steps.discard('discovery')

        dry_run = options['dry_run']

        # 2. Resolve step order
        try:
            steps = resolve_step_order(config, requested_steps or None)
        except ValueError as e:
            raise CommandError(str(e))

        # 3. Dry run — show plan and exit
        if dry_run:
            self._dry_run(config, steps, run_discovery, options)
            return

        # 4. Create PipelineBatchRun
        batch = PipelineBatchRun.objects.create(
            pipeline_id=config['id'],
            mode=RunMode.BOOTSTRAP if not MigratedCoin.objects.exists() else RunMode.STEADY_STATE,
            status=RunStatus.STARTED,
            started_at=timezone.now(),
        )

        total_succeeded = 0
        total_failed = 0
        total_skipped = 0

        try:
            # 5. Run discovery (if requested)
            if run_discovery:
                discovery_handler = config['discovery']['handler']
                logger.info("Running discovery...")
                self.stdout.write("Running discovery...")
                try:
                    discovery_result = call_handler(
                        discovery_handler, config,
                        days=options.get('days'),
                    )
                    logger.info(
                        "Discovery: %d new, %d updated",
                        discovery_result.get('created', 0),
                        discovery_result.get('updated', 0),
                    )
                    self.stdout.write(
                        f"Discovery: {discovery_result.get('created', 0)} new, "
                        f"{discovery_result.get('updated', 0)} updated"
                    )
                except Exception as e:
                    logger.error("Discovery failed: %s", e, exc_info=True)
                    self.stderr.write(f"Discovery failed: {e}")
                    # Discovery failure is non-fatal for per-coin steps
                    # (there may already be coins in the database)

            # 6. Build work list
            coins = get_coins_to_process(
                config,
                days=options.get('days'),
                max_coins=options.get('coins'),
            )
            self.stdout.write(f"Processing {len(coins)} coins")

            # 7. Run each step in dependency order
            for step in steps:
                if not step.get('per_coin', False):
                    continue

                step_name = step['name']
                logger.info("Step '%s': %d coins to process", step_name, len(coins))
                self.stdout.write(f"\nStep '{step_name}': {len(coins)} coins")

                succeeded = 0
                failed = 0
                skipped = 0

                for coin in coins:
                    if should_skip(coin, step):
                        skipped += 1
                        continue

                    try:
                        result = call_handler(step['handler'], coin, config)
                        update_pipeline_status(coin, step, result)
                        succeeded += 1

                        # Log per-coin result for feature layers
                        records = result.get('records_loaded')
                        if records is not None:
                            logger.info(
                                "%s %s: %d records loaded",
                                step_name, coin.mint_address, records,
                            )
                    except Exception as e:
                        logger.error(
                            "%s failed for %s: %s",
                            step_name, coin.mint_address, e,
                            exc_info=True,
                        )
                        mark_error(coin, step, str(e))
                        failed += 1

                    # Rate limit
                    sleep_time = step.get('rate_limit_sleep', 0)
                    if sleep_time:
                        time.sleep(sleep_time)

                total_succeeded += succeeded
                total_failed += failed
                total_skipped += skipped

                summary = (
                    f"Step '{step_name}': "
                    f"{succeeded} succeeded, {failed} failed, {skipped} skipped"
                )
                logger.info(summary)
                self.stdout.write(summary)

            # 8. Update batch
            batch.status = RunStatus.COMPLETE
            batch.completed_at = timezone.now()
            batch.coins_attempted = total_succeeded + total_failed
            batch.coins_succeeded = total_succeeded
            batch.coins_failed = total_failed
            batch.save()

        except Exception as e:
            batch.status = RunStatus.ERROR
            batch.completed_at = timezone.now()
            batch.error_message = str(e)
            batch.coins_attempted = total_succeeded + total_failed
            batch.coins_succeeded = total_succeeded
            batch.coins_failed = total_failed
            batch.save()
            raise CommandError(f"Orchestrator failed: {e}")

        self.stdout.write(
            f"\nComplete: {total_succeeded} succeeded, "
            f"{total_failed} failed, {total_skipped} skipped"
        )

    def _dry_run(self, config, steps, run_discovery, options):
        """Show what would be executed without doing it."""
        self.stdout.write(f"=== DRY RUN: {config['id']} — {config['name']} ===\n")

        if run_discovery:
            self.stdout.write(
                f"1. Discovery: {config['discovery']['source']} "
                f"(handler: {config['discovery']['handler']})"
            )

        coins = get_coins_to_process(
            config,
            days=options.get('days'),
            max_coins=options.get('coins'),
        )
        self.stdout.write(f"\nCoins to process: {len(coins)}")

        if options.get('days'):
            self.stdout.write(f"  (filtered to last {options['days']} days)")
        if options.get('coins'):
            self.stdout.write(f"  (limited to {options['coins']} coins)")

        for i, step in enumerate(steps, start=2 if run_discovery else 1):
            skip_count = sum(1 for c in coins if should_skip(c, step))
            process_count = len(coins) - skip_count
            self.stdout.write(
                f"\n{i}. Step '{step['name']}':"
                f"\n   Handler: {step['handler']}"
                f"\n   Source: {step.get('source', 'n/a')}"
                f"\n   Rate limit: {step.get('rate_limit_sleep', 0)}s between calls"
                f"\n   Would process: {process_count} coins"
                f"\n   Would skip: {skip_count} coins (condition: {step.get('skip_if', 'none')})"
            )

        self.stdout.write("\n=== END DRY RUN ===")
