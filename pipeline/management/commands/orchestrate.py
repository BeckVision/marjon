"""Management command: pipeline orchestrator.

Chains all pipeline steps together for a universe, runs them in dependency
order, processes coins in batches, tracks progress, and supports resume.

Usage:
    python manage.py orchestrate --universe u001 --days 7
    python manage.py orchestrate --universe u001 --steps discovery,ohlcv
    python manage.py orchestrate --universe u001 --resume
    python manage.py orchestrate --universe u001 --coins 5 --dry-run
    python manage.py orchestrate --universe u001 --steps ohlcv --workers 6
"""

import importlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import (
    BaseCommand,
    CommandError,
    OutputWrapper,
    connections,
    DEFAULT_DB_ALIAS,
    color_style,
    no_style,
)
from django.utils import timezone

from pipeline.exceptions import BudgetExhausted
from pipeline.orchestration.utils import (
    call_handler,
    get_coins_to_process,
    get_persistent_failures,
    load_universe_config,
    mark_error,
    resolve_step_order,
    should_skip,
    update_pipeline_status,
)
from warehouse.models import PipelineBatchRun, RunMode, RunStatus

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the full pipeline for a universe: discovery → pool mapping → feature layers"

    def execute(self, *args, **options):
        if options["force_color"] and options["no_color"]:
            raise CommandError(
                "The --no-color and --force-color options can't be used together."
            )
        if options["force_color"]:
            self.style = color_style(force_color=True)
        elif options["no_color"]:
            self.style = no_style()
            self.stderr.style_func = None
        if options.get("stdout"):
            self.stdout = OutputWrapper(options["stdout"])
        if options.get("stderr"):
            self.stderr = OutputWrapper(options["stderr"])

        if self.requires_system_checks and not options["skip_checks"]:
            check_kwargs = self.get_check_kwargs(options)
            self.check(**check_kwargs)
        if self.requires_migrations_checks:
            self.check_migrations()
        output = self.handle(*args, **options)
        if isinstance(output, str) and output:
            if self.output_transaction:
                connection = connections[options.get("database", DEFAULT_DB_ALIAS)]
                output = "%s\n%s\n%s" % (
                    self.style.SQL_KEYWORD(connection.ops.start_transaction_sql()),
                    output,
                    self.style.SQL_KEYWORD(connection.ops.end_transaction_sql()),
                )
            self.stdout.write(output)
        return output

    @staticmethod
    def _step_context(config, step):
        """Merge universe-level and step-level config for handler consumption."""
        merged = dict(config)
        merged.update(step)
        merged['universe_config'] = config
        return merged

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
            '--mature-only', action='store_true',
            help='Only include mature event-driven assets in the per-step work list',
        )
        parser.add_argument(
            '--workers', type=int, default=None,
            help='Number of concurrent workers (overrides step config; 1 = serial)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be executed without doing it',
        )
        parser.add_argument(
            '--retry-failed', action='store_true',
            help='Bypass consecutive failure skip — retry persistently failing coins',
        )
        parser.add_argument(
            '--loops', type=int, default=1,
            help='Run the full cycle N times. Each loop advances watermarks. '
                 'Use for backfill: --loops 730 crawls ~2 years (1 day/loop).',
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
        retry_failed = options.get('retry_failed', False)

        # 2. Resolve step order
        try:
            steps = resolve_step_order(config, requested_steps or None)
        except ValueError as e:
            raise CommandError(str(e))

        # 3. Dry run — show plan and exit
        if dry_run:
            self._dry_run(config, steps, run_discovery, options, retry_failed)
            return

        # 4. Create PipelineBatchRun
        # Resolve universe model from config to avoid paradigm leak
        model_path = config['model']
        module_path, class_name = model_path.rsplit('.', 1)
        universe_model = getattr(importlib.import_module(module_path), class_name)

        batch = PipelineBatchRun.objects.create(
            pipeline_id=config['id'],
            mode=RunMode.BOOTSTRAP if not universe_model.objects.exists() else RunMode.STEADY_STATE,
            status=RunStatus.STARTED,
            started_at=timezone.now(),
        )

        total_succeeded = 0
        total_failed = 0
        total_skipped = 0
        num_loops = options.get('loops', 1)
        run_summary = {
            'universe': config['id'],
            'dry_run': False,
            'loops': num_loops,
            'total_succeeded': 0,
            'total_failed': 0,
            'total_skipped': 0,
            'discovery': None,
            'steps': {},
        }

        try:
            # 5. Run discovery (if requested) — only on first loop
            if run_discovery and config.get('discovery'):
                discovery_handler = config['discovery']['handler']
                logger.info("Running discovery...")
                self.stdout.write("Running discovery...")
                try:
                    discovery_result = call_handler(
                        discovery_handler, config,
                        days=options.get('days'),
                    )
                    run_summary['discovery'] = {
                        'created': discovery_result.get('created', 0),
                        'updated': discovery_result.get('updated', 0),
                    }
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

            for loop_num in range(1, num_loops + 1):
                if num_loops > 1:
                    self.stdout.write(
                        f"\n{'='*60}\n"
                        f"Loop {loop_num}/{num_loops}\n"
                        f"{'='*60}"
                    )

                # 6. Build work list (refreshed each loop — watermarks change)
                coins = get_coins_to_process(
                    config,
                    days=options.get('days'),
                    max_coins=options.get('coins'),
                    mature_only=options.get('mature_only', False),
                )
                if num_loops == 1:
                    self.stdout.write(f"Processing {len(coins)} coins")

                # 7. Run each step in dependency order
                loop_succeeded = 0
                loop_failed = 0
                loop_skipped = 0

                for step in steps:
                    step_name = step['name']

                    if not step.get('per_coin', False):
                        # --- batch step handling (only on first loop) ---
                        if loop_num > 1:
                            continue
                        logger.info("Step '%s': batch mode", step_name)
                        self.stdout.write(f"\nStep '{step_name}': batch mode")
                        step_summary = run_summary['steps'].setdefault(
                            step_name,
                            {
                                'mode': 'batch',
                                'mapped': 0,
                                'unmapped': 0,
                                'failed': 0,
                                'skipped': 0,
                            },
                        )

                        batch_coins = [c for c in coins if not should_skip(c, step, retry_failed, config)]
                        if not batch_coins:
                            self.stdout.write(f"Step '{step_name}': all coins skipped")
                            step_summary['skipped'] += len(coins)
                            continue

                        try:
                            step_context = self._step_context(config, step)
                            result = call_handler(step['handler'], batch_coins, step_context)
                            mapped = result['dexscreener_mapped'] + result['geckoterminal_mapped']
                            unmapped = result['unmapped']
                            loop_succeeded += mapped
                            step_summary['mapped'] += mapped
                            step_summary['unmapped'] += unmapped
                            logger.info(
                                "%s: %d mapped, %d unmapped",
                                step_name, mapped, unmapped,
                            )
                            self.stdout.write(
                                f"Step '{step_name}': {mapped} mapped, {unmapped} unmapped"
                            )
                        except Exception as e:
                            logger.error("%s failed: %s", step_name, e, exc_info=True)
                            self.stderr.write(f"Step '{step_name}' failed: {e}")
                            loop_failed += len(batch_coins)
                            step_summary['failed'] += len(batch_coins)
                        continue
                        # --- END batch step handling ---

                    # Resolve worker count: CLI flag > step config > 1
                    workers = options.get('workers') or step.get('workers', 1)

                    if num_loops == 1:
                        logger.info(
                            "Step '%s': %d coins to process (workers=%d)",
                            step_name, len(coins), workers,
                        )
                        self.stdout.write(
                            f"\nStep '{step_name}': {len(coins)} coins (workers={workers})"
                        )

                    # Filter coins that should be skipped
                    work_coins = [c for c in coins if not should_skip(c, step, retry_failed, config)]
                    skipped = len(coins) - len(work_coins)

                    if not work_coins:
                        loop_skipped += skipped
                        step_summary = run_summary['steps'].setdefault(
                            step_name,
                            {
                                'mode': 'per_coin',
                                'succeeded': 0,
                                'failed': 0,
                                'skipped': 0,
                                'records_loaded': 0,
                            },
                        )
                        step_summary['skipped'] += skipped
                        continue

                    step_context = self._step_context(config, step)
                    if workers > 1:
                        succeeded, failed, records_loaded = self._run_concurrent(
                            step, work_coins, step_context, workers,
                        )
                    else:
                        succeeded, failed, records_loaded = self._run_serial(
                            step, work_coins, step_context,
                        )

                    loop_succeeded += succeeded
                    loop_failed += failed
                    loop_skipped += skipped
                    step_summary = run_summary['steps'].setdefault(
                        step_name,
                        {
                            'mode': 'per_coin',
                            'succeeded': 0,
                            'failed': 0,
                            'skipped': 0,
                            'records_loaded': 0,
                        },
                    )
                    step_summary['succeeded'] += succeeded
                    step_summary['failed'] += failed
                    step_summary['skipped'] += skipped
                    step_summary['records_loaded'] += records_loaded

                    if num_loops == 1:
                        summary = (
                            f"Step '{step_name}': "
                            f"{succeeded} succeeded, {failed} failed, {skipped} skipped"
                        )
                        logger.info(summary)
                        self.stdout.write(summary)

                total_succeeded += loop_succeeded
                total_failed += loop_failed
                total_skipped += loop_skipped

                if num_loops > 1 and loop_num % 10 == 0:
                    self.stdout.write(
                        f"  Progress: loop {loop_num}/{num_loops}, "
                        f"total {total_succeeded} succeeded, {total_failed} failed"
                    )

            # 8. Update batch
            batch.status = RunStatus.COMPLETE
            batch.completed_at = timezone.now()
            batch.coins_attempted = total_succeeded + total_failed
            batch.coins_succeeded = total_succeeded
            batch.coins_failed = total_failed
            batch.save()
            run_summary['total_succeeded'] = total_succeeded
            run_summary['total_failed'] = total_failed
            run_summary['total_skipped'] = total_skipped

        except Exception as e:
            batch.status = RunStatus.ERROR
            batch.completed_at = timezone.now()
            batch.error_message = str(e)
            batch.coins_attempted = total_succeeded + total_failed
            batch.coins_succeeded = total_succeeded
            batch.coins_failed = total_failed
            batch.save()
            raise CommandError(f"Orchestrator failed: {e}")

        # Failure summary — surface persistently failing coins
        layer_ids = [s['layer_id'] for s in steps if s.get('layer_id')]
        if layer_ids:
            persistent = get_persistent_failures(layer_ids, config=config)
            if persistent:
                self.stdout.write("\n--- Persistent failures ---")
                for pf in persistent:
                    self.stdout.write(
                        f"  {pf['asset_id']} / {pf['layer_id']}: "
                        f"{pf['consecutive_errors']} consecutive errors"
                    )
                self.stdout.write(
                    f"Total: {len(persistent)} coin/layer pairs stuck in error. "
                    f"Use --retry-failed to force retry."
                )

        self.stdout.write(
            f"\nComplete: {total_succeeded} succeeded, "
            f"{total_failed} failed, {total_skipped} skipped"
        )
        return run_summary

    def _run_serial(self, step, coins, config):
        """Process coins one at a time. Returns (succeeded, failed)."""
        step_name = step['name']
        succeeded = 0
        failed = 0
        records_loaded = 0

        for coin in coins:
            try:
                result = call_handler(step['handler'], coin, config)
                update_pipeline_status(coin, step, result, config)
                succeeded += 1

                records = result.get('records_loaded')
                if records is not None:
                    records_loaded += records
                    logger.info(
                        "%s %s: %d records loaded",
                        step_name, coin, records,
                    )
            except BudgetExhausted as e:
                self.stdout.write(
                    f"Step '{step_name}': budget exhausted — stopping step. "
                    f"({succeeded} succeeded so far)"
                )
                logger.warning("%s: budget exhausted after %d coins", step_name, succeeded)
                break
            except Exception as e:
                logger.error(
                    "%s failed for %s: %s",
                    step_name, coin, e,
                    exc_info=True,
                )
                mark_error(coin, step, str(e), config)
                failed += 1

            sleep_time = step.get('rate_limit_sleep', 0)
            if sleep_time:
                time.sleep(sleep_time)

        return succeeded, failed, records_loaded

    def _run_concurrent(self, step, coins, config, workers):
        """Process coins concurrently with ThreadPoolExecutor.

        Each worker gets its own Django DB connection (thread-local).
        Each coin writes to disjoint rows, so no locking needed.

        Returns (succeeded, failed, records_loaded).
        """
        step_name = step['name']
        succeeded = 0
        failed = 0
        records_loaded = 0

        def _process_coin(coin):
            result = call_handler(step['handler'], coin, config)
            update_pipeline_status(coin, step, result, config)
            return coin, result

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_process_coin, coin): coin
                for coin in coins
            }

            for future in as_completed(futures):
                coin = futures[future]
                try:
                    _, result = future.result()
                    succeeded += 1

                    records = result.get('records_loaded')
                    if records is not None:
                        records_loaded += records
                        logger.info(
                            "%s %s: %d records loaded",
                            step_name, coin, records,
                        )
                except Exception as e:
                    logger.error(
                        "%s failed for %s: %s",
                        step_name, coin, e,
                        exc_info=True,
                    )
                    mark_error(coin, step, str(e), config)
                    failed += 1

        return succeeded, failed, records_loaded

    def _dry_run(self, config, steps, run_discovery, options, retry_failed=False):
        """Show what would be executed without doing it."""
        self.stdout.write(f"=== DRY RUN: {config['id']} — {config['name']} ===\n")

        if retry_failed:
            self.stdout.write("  (--retry-failed: bypassing consecutive failure skip)")

        if run_discovery:
            self.stdout.write(
                f"1. Discovery: {config['discovery']['source']} "
                f"(handler: {config['discovery']['handler']})"
            )

        coins = get_coins_to_process(
            config,
            days=options.get('days'),
            max_coins=options.get('coins'),
            mature_only=options.get('mature_only', False),
        )
        self.stdout.write(f"\nCoins to process: {len(coins)}")

        if options.get('days'):
            self.stdout.write(f"  (filtered to last {options['days']} days)")
        if options.get('coins'):
            self.stdout.write(f"  (limited to {options['coins']} coins)")

        workers_override = options.get('workers')

        for i, step in enumerate(steps, start=2 if run_discovery else 1):
            skip_count = sum(1 for c in coins if should_skip(c, step, retry_failed, config))
            process_count = len(coins) - skip_count
            step_workers = workers_override or step.get('workers', 1)
            self.stdout.write(
                f"\n{i}. Step '{step['name']}':"
                f"\n   Handler: {step['handler']}"
                f"\n   Source: {step.get('source', 'n/a')}"
                f"\n   Workers: {step_workers}"
                f"\n   Rate limit: {step.get('rate_limit_sleep', 0)}s between calls"
                f"\n   Would process: {process_count} coins"
                f"\n   Would skip: {skip_count} coins (condition: {step.get('skip_if', 'none')})"
            )

        self.stdout.write("\n=== END DRY RUN ===")
