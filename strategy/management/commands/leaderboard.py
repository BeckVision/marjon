"""Management command: leaderboard — rank backtest runs by metric.

Usage:
    python manage.py leaderboard
    python manage.py leaderboard --strategy u001_volume_spike_v1
    python manage.py leaderboard --top 20 --sort total_pnl
    python manage.py leaderboard --sweep-id my_sweep_123
"""

from django.core.management.base import BaseCommand

from strategy.analysis.leaderboard import build_leaderboard
from strategy.models import U001BacktestResult


class Command(BaseCommand):
    help = "Rank backtest runs by any metric"

    def add_arguments(self, parser):
        parser.add_argument(
            '--strategy', default=None,
            help='Filter by strategy_id',
        )
        parser.add_argument(
            '--top', type=int, default=10,
            help='Number of results to show (default: 10)',
        )
        parser.add_argument(
            '--sort', default='sharpe_ratio',
            help='Metric to sort by (default: sharpe_ratio)',
        )
        parser.add_argument(
            '--sweep-id', default=None,
            help='Filter to a specific sweep_id',
        )

    def handle(self, **options):
        qs = U001BacktestResult.objects.filter(
            run__status='completed',
        )

        if options['strategy']:
            qs = qs.filter(run__strategy_id=options['strategy'])

        if options['sweep_id']:
            qs = qs.filter(run__sweep_id=options['sweep_id'])

        try:
            rows = build_leaderboard(qs, options['sort'], options['top'])
        except ValueError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            return

        if not rows:
            self.stdout.write("No completed backtest results found.")
            return

        self.stdout.write(self.style.SUCCESS(
            f"\n--- Leaderboard (top {len(rows)}, sorted by {options['sort']}) ---"
        ))

        for row in rows:
            self.stdout.write(
                f"#{row['rank']:>3}  Run {row['run_id']:<5}  "
                f"{row['strategy_id']} v{row['strategy_version']}  "
                f"trades={row['total_trades']:<4}  "
                f"win={row['win_rate'] or '-':>8}  "
                f"PnL={row['total_pnl']:>12}  "
                f"sharpe={row['sharpe_ratio'] or '-':>8}  "
                f"sortino={row['sortino_ratio'] or '-':>8}  "
                f"dd={row['max_drawdown_pct'] or '-':>8}  "
                f"{row['sort_value'] or '-'}"
            )
