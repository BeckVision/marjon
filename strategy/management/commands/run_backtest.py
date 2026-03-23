"""Management command: run a backtest.

Usage:
    python manage.py run_backtest --strategy u001_volume_spike_v1
    python manage.py run_backtest --strategy u001_volume_spike_v1 --coins 10
    python manage.py run_backtest --strategy u001_volume_spike_v1 --label "first run"
    python manage.py run_backtest --strategy u001_volume_spike_v1 --dry-run
"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from strategy.engine.runner import BacktestRunner
from strategy.models import (
    BacktestStatus,
    U001BacktestResult,
    U001BacktestRun,
    U001BacktestTrade,
)
from strategy.strategies import load_strategy_config
from warehouse.models import MigratedCoin, U001PipelineStatus

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run a backtest for a strategy"

    def add_arguments(self, parser):
        parser.add_argument(
            '--strategy', required=True,
            help='Strategy name (maps to strategy/strategies/{name}.py)',
        )
        parser.add_argument(
            '--coins', type=int, default=None,
            help='Limit to N coins (default: all with complete OHLCV data)',
        )
        parser.add_argument(
            '--label', default='',
            help='Human-readable label for this run',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Load config and resolve assets but do not execute',
        )

    def handle(self, **options):
        # 1. Load strategy config
        try:
            config = load_strategy_config(options['strategy'])
        except ValueError as e:
            raise CommandError(str(e))

        self.stdout.write(f"Strategy: {config['name']} v{config['version']}")
        self.stdout.write(f"Hypothesis: {config['hypothesis']}")

        # 2. Resolve asset IDs — coins with complete OHLCV data
        asset_ids = self._resolve_assets(config, options['coins'])
        if not asset_ids:
            raise CommandError("No eligible assets found")

        self.stdout.write(f"Assets: {len(asset_ids)}")

        # 3. Determine data window from asset observation windows
        data_start, data_end = self._resolve_data_window(asset_ids)
        self.stdout.write(f"Data window: {data_start} → {data_end}")

        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f"[DRY RUN] Would test {len(asset_ids)} assets "
                f"with strategy '{config['id']}'"
            ))
            return

        # 4. Create run record
        now = timezone.now()
        run = U001BacktestRun.objects.create(
            strategy_id=config['id'],
            strategy_version=config['version'],
            run_label=options['label'],
            data_start=data_start,
            data_end=data_end,
            params_snapshot=config,
            started_at=now,
            status=BacktestStatus.RUNNING,
        )
        self.stdout.write(f"Run ID: {run.pk}")

        # 5. Execute
        try:
            runner = BacktestRunner(config, asset_ids, data_start, data_end)
            result = runner.run()
        except Exception as e:
            run.status = BacktestStatus.FAILED
            run.error_message = str(e)
            run.completed_at = timezone.now()
            run.save()
            raise CommandError(f"Backtest failed: {e}")

        # 6. Save results
        run.status = BacktestStatus.COMPLETED
        run.completed_at = timezone.now()
        run.entities_tested = result['entities_tested']
        run.save()

        metrics = result['metrics']
        U001BacktestResult.objects.create(run=run, **metrics)

        # Save individual trades
        trade_objs = []
        for t in result['trades']:
            trade_objs.append(U001BacktestTrade(
                run=run,
                coin_id=t.asset_id,
                entry_time=t.entry_time,
                exit_time=t.exit_time,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                entry_amount=t.amount,
                entry_reason=t.entry_reason,
                exit_reason=t.exit_reason,
                pnl=t.pnl,
                roi_pct=t.roi_pct,
                hold_minutes=t.hold_minutes,
            ))
        if trade_objs:
            U001BacktestTrade.objects.bulk_create(trade_objs)

        # 7. Print summary
        self.stdout.write(self.style.SUCCESS("\n--- Backtest Complete ---"))
        self.stdout.write(f"Run ID:         {run.pk}")
        self.stdout.write(f"Entities tested: {result['entities_tested']}")
        self.stdout.write(f"Total trades:   {metrics['total_trades']}")
        self.stdout.write(f"Win rate:       {metrics['win_rate']}")
        self.stdout.write(f"Total PnL:      {metrics['total_pnl']}")
        self.stdout.write(f"Profit factor:  {metrics['profit_factor']}")
        self.stdout.write(f"Sharpe ratio:   {metrics['sharpe_ratio']}")
        self.stdout.write(f"Sortino ratio:  {metrics['sortino_ratio']}")
        self.stdout.write(f"Max drawdown:   {metrics['max_drawdown_pct']}")
        self.stdout.write(f"Avg hold (min): {metrics['avg_hold_minutes']}")

    def _resolve_assets(self, config, limit):
        """Return mint_address list for coins with complete FL-001 data."""
        complete_statuses = U001PipelineStatus.objects.filter(
            layer_id='FL-001',
            status='window_complete',
        ).values_list('coin_id', flat=True)

        asset_ids = list(complete_statuses)

        if limit:
            asset_ids = asset_ids[:limit]

        return asset_ids

    def _resolve_data_window(self, asset_ids):
        """Determine the broadest common data window across assets."""
        coins = MigratedCoin.objects.filter(mint_address__in=asset_ids)

        starts = []
        ends = []
        for coin in coins:
            if coin.anchor_event:
                starts.append(
                    coin.anchor_event + MigratedCoin.OBSERVATION_WINDOW_START
                )
                ends.append(
                    coin.anchor_event + MigratedCoin.OBSERVATION_WINDOW_END
                )

        if not starts:
            raise CommandError("No assets with anchor events found")

        # Use the earliest start and latest end to cover all assets
        return min(starts), max(ends)
