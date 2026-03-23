"""Management command: sweep_params — parameter sweep grid search.

Usage:
    python manage.py sweep_params --strategy u001_volume_spike_v1
    python manage.py sweep_params --strategy u001_volume_spike_v1 --coins 5
    python manage.py sweep_params --strategy u001_volume_spike_v1 --dry-run
    python manage.py sweep_params --strategy u001_volume_spike_v1 --label "first sweep"
"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from strategy.engine.sweep import generate_param_grid, run_sweep
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
    help = "Run a parameter sweep for a strategy"

    def add_arguments(self, parser):
        parser.add_argument(
            '--strategy', required=True,
            help='Strategy name (maps to strategy/strategies/{name}.py)',
        )
        parser.add_argument(
            '--coins', type=int, default=None,
            help='Limit to N coins',
        )
        parser.add_argument(
            '--label', default='',
            help='Label prefix for sweep runs',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show grid size and params without executing',
        )

    def handle(self, **options):
        try:
            config = load_strategy_config(options['strategy'])
        except ValueError as e:
            raise CommandError(str(e))

        self.stdout.write(f"Strategy: {config['name']} v{config['version']}")

        # Preview grid
        grid = generate_param_grid(config)
        self.stdout.write(f"Parameter grid: {len(grid)} combinations")

        if grid and grid[0]:
            self.stdout.write("Sweep axes:")
            sample = grid[0]
            for key in sorted(sample.keys()):
                values = sorted(set(combo[key] for combo in grid))
                self.stdout.write(f"  {key}: {values}")

        # Resolve assets
        asset_ids = self._resolve_assets(config, options['coins'])
        if not asset_ids:
            raise CommandError("No eligible assets found")

        self.stdout.write(f"Assets: {len(asset_ids)}")

        data_start, data_end = self._resolve_data_window(asset_ids)
        self.stdout.write(f"Data window: {data_start} -> {data_end}")

        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f"[DRY RUN] Would run {len(grid)} combinations "
                f"across {len(asset_ids)} assets"
            ))
            return

        # Execute sweep
        sweep_id, results = run_sweep(
            config, asset_ids, data_start, data_end,
        )

        self.stdout.write(f"\nSweep ID: {sweep_id}")

        # Save results
        for combo, result in results:
            now = timezone.now()
            label = options['label']
            combo_str = ', '.join(f"{k}={v}" for k, v in sorted(combo.items()))
            run_label = f"{label} [{combo_str}]".strip() if combo_str else label

            run = U001BacktestRun.objects.create(
                strategy_id=config['id'],
                strategy_version=config['version'],
                run_label=run_label,
                sweep_id=sweep_id,
                data_start=data_start,
                data_end=data_end,
                params_snapshot=result.get('combination', combo),
                started_at=now,
                completed_at=now,
                status=BacktestStatus.COMPLETED,
                entities_tested=result['entities_tested'],
            )

            U001BacktestResult.objects.create(run=run, **result['metrics'])

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

        # Summary
        self.stdout.write(self.style.SUCCESS(
            f"\n--- Sweep Complete: {len(results)} runs ---"
        ))
        for combo, result in results:
            m = result['metrics']
            combo_str = ', '.join(f"{k}={v}" for k, v in sorted(combo.items()))
            self.stdout.write(
                f"  {combo_str or 'default'}: "
                f"trades={m['total_trades']}  "
                f"PnL={m['total_pnl']}  "
                f"sharpe={m.get('sharpe_ratio', '-')}  "
                f"win_rate={m.get('win_rate', '-')}"
            )

    def _resolve_assets(self, config, limit):
        complete_statuses = U001PipelineStatus.objects.filter(
            layer_id='FL-001', status='window_complete',
        ).values_list('coin_id', flat=True)
        asset_ids = list(complete_statuses)
        if limit:
            asset_ids = asset_ids[:limit]
        return asset_ids

    def _resolve_data_window(self, asset_ids):
        coins = MigratedCoin.objects.filter(mint_address__in=asset_ids)
        starts, ends = [], []
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
        return min(starts), max(ends)
