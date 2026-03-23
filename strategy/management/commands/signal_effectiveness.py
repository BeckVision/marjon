"""Management command: signal_effectiveness — per-signal analysis.

Usage:
    python manage.py signal_effectiveness
    python manage.py signal_effectiveness --run-id 5
    python manage.py signal_effectiveness --strategy u001_volume_spike_v1
    python manage.py signal_effectiveness --sweep-id my_sweep
"""

from django.core.management.base import BaseCommand, CommandError

from strategy.analysis.signal_effectiveness import (
    analyze_signal_correlation,
    analyze_signal_effectiveness,
)
from strategy.models import U001BacktestTrade


class Command(BaseCommand):
    help = "Analyze per-signal effectiveness across backtest trades"

    def add_arguments(self, parser):
        parser.add_argument(
            '--run-id', type=int, default=None,
            help='Analyze trades from a specific run ID',
        )
        parser.add_argument(
            '--strategy', default=None,
            help='Filter by strategy_id',
        )
        parser.add_argument(
            '--sweep-id', default=None,
            help='Filter to a specific sweep_id',
        )

    def handle(self, **options):
        qs = U001BacktestTrade.objects.filter(run__status='completed')

        if options['run_id']:
            qs = qs.filter(run_id=options['run_id'])
        if options['strategy']:
            qs = qs.filter(run__strategy_id=options['strategy'])
        if options['sweep_id']:
            qs = qs.filter(run__sweep_id=options['sweep_id'])

        trades = list(qs)
        if not trades:
            self.stdout.write("No trades found matching filters.")
            return

        self.stdout.write(f"Analyzing {len(trades)} trades...\n")

        # Per-signal effectiveness
        effectiveness = analyze_signal_effectiveness(trades)
        self.stdout.write(self.style.SUCCESS("--- Signal Effectiveness ---"))
        for sig_id, stats in effectiveness.items():
            self.stdout.write(
                f"  {sig_id}: "
                f"fires={stats['fire_count']}  "
                f"win_rate={stats['win_rate'] or '-'}  "
                f"avg_pnl={stats['avg_pnl']:>12}  "
                f"total_pnl={stats['total_pnl']:>12}"
            )

        # Correlation analysis
        correlation = analyze_signal_correlation(trades)
        if correlation:
            self.stdout.write(self.style.SUCCESS("\n--- Signal Correlation ---"))
            for (sig_a, sig_b), stats in correlation.items():
                self.stdout.write(
                    f"  {sig_a} + {sig_b}: "
                    f"co_fires={stats['co_fire_count']}  "
                    f"co_avg_pnl={stats['co_avg_pnl'] or '-':>12}  "
                    f"only_{sig_a}={stats['only_a_count']}  "
                    f"only_{sig_b}={stats['only_b_count']}"
                )
