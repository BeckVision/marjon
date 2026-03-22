"""Shelf 4 audit command: post-insert warehouse data quality checks.

Periodic checks that flag anomalies without blocking data. Produces
a report to stdout — does not modify any data.

Usage:
    python manage.py audit_warehouse
    python manage.py audit_warehouse --checks coverage,gaps
    python manage.py audit_warehouse --layer FL-001
    python manage.py audit_warehouse --coins 100
"""

import logging
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Min

from warehouse.models import (
    MigratedCoin, OHLCVCandle, HolderSnapshot, RawTransaction,
    PipelineCompleteness, U001PipelineStatus,
)

logger = logging.getLogger(__name__)

LAYER_MODELS = {
    'FL-001': OHLCVCandle,
    'FL-002': HolderSnapshot,
    'RD-001': RawTransaction,
}

ALL_CHECKS = ['coverage', 'sparse', 'gaps', 'first_obs', 'orphans']


class Command(BaseCommand):
    help = "Run Shelf 4 audit checks on warehouse data quality"

    def add_arguments(self, parser):
        parser.add_argument(
            '--checks', type=str, default=None,
            help=f'Comma-separated checks to run (default: all). Options: {",".join(ALL_CHECKS)}',
        )
        parser.add_argument(
            '--layer', type=str, default=None,
            help='Limit to one layer (FL-001, FL-002, RD-001). Default: all.',
        )
        parser.add_argument(
            '--coins', type=int, default=None,
            help='Limit per-coin checks to N most recent coins.',
        )

    def handle(self, *args, **options):
        checks = set(options['checks'].split(',')) if options['checks'] else set(ALL_CHECKS)
        layer_filter = options.get('layer')
        max_coins = options.get('coins')

        layers = {layer_filter: LAYER_MODELS[layer_filter]} if layer_filter else LAYER_MODELS

        self.stdout.write("=" * 60)
        self.stdout.write("WAREHOUSE AUDIT REPORT")
        self.stdout.write("=" * 60)

        coins = MigratedCoin.objects.order_by('-anchor_event')
        if max_coins:
            coins = coins[:max_coins]
        coins = list(coins)
        self.stdout.write(f"\nUniverse: {len(coins)} coins")

        if 'coverage' in checks:
            self._check_coverage(coins, layers)
        if 'sparse' in checks:
            self._check_sparse(coins, layers)
        if 'gaps' in checks:
            self._check_gaps(coins, layers)
        if 'first_obs' in checks:
            self._check_first_obs(coins, layers)
        if 'orphans' in checks:
            self._check_orphans(layers)

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("END AUDIT")
        self.stdout.write("=" * 60)

    def _check_coverage(self, coins, layers):
        """Pipeline status breakdown and record counts per layer."""
        self.stdout.write("\n--- Coverage Summary ---")

        # Pipeline status breakdown
        status_counts = (
            U001PipelineStatus.objects
            .values('layer_id', 'status')
            .annotate(count=Count('id'))
            .order_by('layer_id', 'status')
        )
        by_layer = {}
        for row in status_counts:
            by_layer.setdefault(row['layer_id'], []).append(
                f"{row['status']}: {row['count']}"
            )
        for layer_id, statuses in sorted(by_layer.items()):
            self.stdout.write(f"\n  {layer_id} pipeline status:")
            for s in statuses:
                self.stdout.write(f"    {s}")

        # Record counts per layer
        for layer_id, model in layers.items():
            total = model.objects.count()
            coin_count = model.objects.values('coin_id').distinct().count()
            self.stdout.write(
                f"\n  {layer_id}: {total:,} records across {coin_count} coins"
            )

        # Coins with no data at all
        coin_ids = {c.mint_address for c in coins}
        for layer_id, model in layers.items():
            coins_with_data = set(
                model.objects.filter(coin_id__in=coin_ids)
                .values_list('coin_id', flat=True).distinct()
            )
            no_data = len(coin_ids) - len(coins_with_data)
            if no_data:
                self.stdout.write(f"  {layer_id}: {no_data} coins have zero records")

    def _check_sparse(self, coins, layers):
        """Flag coins with very few records relative to observation window."""
        self.stdout.write("\n--- Sparse Data Check ---")

        for layer_id, model in layers.items():
            resolution = getattr(model, 'TEMPORAL_RESOLUTION', None)
            if not resolution:
                continue

            window_minutes = MigratedCoin.OBSERVATION_WINDOW_END.total_seconds() / 60
            max_candles = int(window_minutes / (resolution.total_seconds() / 60))
            sparse_threshold = max_candles * 0.01  # < 1% of theoretical max

            coin_ids = [c.mint_address for c in coins if c.is_mature]
            if not coin_ids:
                continue

            counts = (
                model.objects.filter(coin_id__in=coin_ids)
                .values('coin_id')
                .annotate(n=Count('id'))
                .filter(n__lt=sparse_threshold)
                .order_by('n')
            )
            sparse_list = list(counts)

            if sparse_list:
                self.stdout.write(
                    f"\n  {layer_id}: {len(sparse_list)} mature coins with <{int(sparse_threshold)} "
                    f"records (theoretical max: {max_candles})"
                )
                for row in sparse_list[:5]:
                    self.stdout.write(f"    {row['coin_id']}: {row['n']} records")
                if len(sparse_list) > 5:
                    self.stdout.write(f"    ... and {len(sparse_list) - 5} more")
            else:
                self.stdout.write(f"\n  {layer_id}: no sparse coins detected")

    def _check_gaps(self, coins, layers):
        """Detect time gaps in feature layer data for mature coins."""
        self.stdout.write("\n--- Gap Detection ---")

        for layer_id, model in layers.items():
            resolution = getattr(model, 'TEMPORAL_RESOLUTION', None)
            if not resolution:
                self.stdout.write(f"\n  {layer_id}: skipped (no temporal resolution)")
                continue

            gap_tolerance = resolution * 2  # Allow 1 missing interval
            coins_with_gaps = 0
            total_gaps = 0

            mature_coins = [c for c in coins if c.is_mature]
            sample = mature_coins[:100]  # Limit for performance

            for coin in sample:
                timestamps = list(
                    model.objects.filter(coin_id=coin.mint_address)
                    .order_by('timestamp')
                    .values_list('timestamp', flat=True)
                )
                if len(timestamps) < 2:
                    continue

                coin_gaps = 0
                for j in range(1, len(timestamps)):
                    delta = timestamps[j] - timestamps[j - 1]
                    if delta > gap_tolerance:
                        coin_gaps += 1

                if coin_gaps > 0:
                    coins_with_gaps += 1
                    total_gaps += coin_gaps

            self.stdout.write(
                f"\n  {layer_id} (sampled {len(sample)} mature coins): "
                f"{coins_with_gaps} coins with gaps, {total_gaps} total gaps "
                f"(tolerance: {gap_tolerance})"
            )

    def _check_first_obs(self, coins, layers):
        """DQ-006: First observation should be at or near anchor event (T0)."""
        self.stdout.write("\n--- DQ-006: First Observation Check ---")

        late_start_threshold = timedelta(hours=1)

        for layer_id, model in layers.items():
            mature_coins = [c for c in coins if c.is_mature]
            if not mature_coins:
                continue

            late_starts = []
            for coin in mature_coins[:200]:  # Sample for performance
                first_ts = (
                    model.objects.filter(coin_id=coin.mint_address)
                    .aggregate(first=Min('timestamp'))['first']
                )
                if first_ts is None:
                    continue

                delay = first_ts - coin.anchor_event
                if delay > late_start_threshold:
                    late_starts.append({
                        'coin_id': coin.mint_address,
                        'delay_minutes': int(delay.total_seconds() / 60),
                    })

            if late_starts:
                self.stdout.write(
                    f"\n  {layer_id}: {len(late_starts)} coins have first observation "
                    f">{int(late_start_threshold.total_seconds() / 60)}min after anchor"
                )
                for ls in late_starts[:5]:
                    self.stdout.write(
                        f"    {ls['coin_id']}: first obs {ls['delay_minutes']}min after T0"
                    )
                if len(late_starts) > 5:
                    self.stdout.write(f"    ... and {len(late_starts) - 5} more")
            else:
                self.stdout.write(f"\n  {layer_id}: all sampled coins start near T0")

    def _check_orphans(self, layers):
        """Detect data rows whose coin_id doesn't exist in MigratedCoin."""
        self.stdout.write("\n--- Orphan Detection ---")

        all_coin_ids = set(
            MigratedCoin.objects.values_list('mint_address', flat=True)
        )

        for layer_id, model in layers.items():
            layer_coin_ids = set(
                model.objects.values_list('coin_id', flat=True).distinct()
            )
            orphans = layer_coin_ids - all_coin_ids
            if orphans:
                self.stdout.write(
                    f"\n  {layer_id}: {len(orphans)} orphan coin_ids "
                    f"(data exists but no MigratedCoin)"
                )
                for oid in list(orphans)[:5]:
                    self.stdout.write(f"    {oid}")
            else:
                self.stdout.write(f"\n  {layer_id}: no orphans")
