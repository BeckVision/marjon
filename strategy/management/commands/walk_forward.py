"""Management command: walk_forward — anti-overfitting validation.

Usage:
    python manage.py walk_forward --strategy u001_volume_spike_v1
    python manage.py walk_forward --strategy u001_volume_spike_v1 --folds 5
    python manage.py walk_forward --strategy u001_volume_spike_v1 --coins 5 --dry-run
    python manage.py walk_forward --strategy u001_volume_spike_v1 --metric sharpe_ratio
"""

import logging

from django.core.management.base import BaseCommand, CommandError

from strategy.engine.sweep import generate_param_grid
from strategy.engine.walk_forward import generate_folds, run_walk_forward
from strategy.strategies import load_strategy_config
from warehouse.models import MigratedCoin, U001PipelineStatus

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run walk-forward validation for a strategy"

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
            '--folds', type=int, default=5,
            help='Number of walk-forward folds (default: 5)',
        )
        parser.add_argument(
            '--metric', default='sharpe_ratio',
            help='Metric to optimize in IS sweep (default: sharpe_ratio)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show fold structure and grid size without executing',
        )

    def handle(self, **options):
        try:
            config = load_strategy_config(options['strategy'])
        except ValueError as e:
            raise CommandError(str(e))

        self.stdout.write(f"Strategy: {config['name']} v{config['version']}")

        # Resolve assets
        asset_ids = self._resolve_assets(config, options['coins'])
        if not asset_ids:
            raise CommandError("No eligible assets found")

        self.stdout.write(f"Assets: {len(asset_ids)}")

        data_start, data_end = self._resolve_data_window(asset_ids)
        self.stdout.write(f"Data window: {data_start} -> {data_end}")

        n_folds = options['folds']
        grid = generate_param_grid(config)
        self.stdout.write(f"Param grid: {len(grid)} combinations")
        self.stdout.write(f"Folds: {n_folds}")
        self.stdout.write(
            f"Total runs: {len(grid) * n_folds} (sweep) + {n_folds} (OOS)"
        )

        if options['dry_run']:
            folds = generate_folds(data_start, data_end, n_folds)
            for i, (is_s, is_e, oos_s, oos_e) in enumerate(folds, 1):
                self.stdout.write(
                    f"  Fold {i}: IS=[{is_s} → {is_e}]  "
                    f"OOS=[{oos_s} → {oos_e}]"
                )
            self.stdout.write(self.style.SUCCESS("[DRY RUN] No execution."))
            return

        # Execute
        metric = options['metric']
        fold_results = run_walk_forward(
            config, asset_ids, data_start, data_end,
            n_folds=n_folds, metric=metric,
        )

        # Summary
        self.stdout.write(self.style.SUCCESS(
            f"\n--- Walk-Forward Results ({metric}) ---"
        ))
        for fr in fold_results:
            is_val = fr['is_metric_value']
            oos_val = fr['oos_metric_value']
            combo = fr['best_combination'] or {}
            combo_str = ', '.join(
                f"{k}={v}" for k, v in sorted(combo.items())
            ) or 'default'

            is_str = f"{is_val}" if is_val is not None else '-'
            oos_str = f"{oos_val}" if oos_val is not None else '-'

            self.stdout.write(
                f"  Fold {fr['fold_num']}: "
                f"IS={is_str:>10}  OOS={oos_str:>10}  "
                f"params=[{combo_str}]"
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
