"""Persist a daily snapshot of U-001 operational coverage and error state."""

from collections import Counter
from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from warehouse.models import (
    MigratedCoin,
    PipelineCompleteness,
    PoolMapping,
    U001OpsSnapshot,
    U001PipelineStatus,
)

FREE_TIER_GUARD_TEXT = 'exceeds free-tier guard'


def _classify_error_bucket(error):
    text = (error or '').lower()
    if not text:
        return None
    if '401 unauthorized' in text or '403 forbidden' in text:
        return 'auth'
    if FREE_TIER_GUARD_TEXT in text:
        return 'free_tier_guard'
    if (
        'transport_error' in text
        or 'server disconnected' in text
        or 'remoteprotocolerror' in text
        or 'network error' in text
    ):
        return 'transport'
    return 'other'


class Command(BaseCommand):
    help = "Create or update a daily U-001 operational snapshot"

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            default=None,
            help='Snapshot date in YYYY-MM-DD format (default: today in project timezone)',
        )

    def handle(self, *args, **options):
        snapshot_date = self._parse_snapshot_date(options.get('date'))
        counts = self._build_counts()

        snapshot, created = U001OpsSnapshot.objects.update_or_create(
            snapshot_date=snapshot_date,
            defaults=counts,
        )

        action = 'created' if created else 'updated'
        self.stdout.write(
            f"{action} snapshot for {snapshot.snapshot_date.isoformat()}"
        )
        self.stdout.write(
            "counts: "
            f"discovered={snapshot.discovered_count} "
            f"mapped={snapshot.mapped_count} "
            f"rd001_complete={snapshot.rd001_complete_count} "
            f"rd001_partial={snapshot.rd001_partial_count} "
            f"rd001_error={snapshot.rd001_error_count} "
            f"rd001_transport={snapshot.rd001_transport_error_count} "
            f"rd001_guard={snapshot.rd001_guard_error_count} "
            f"fl002_auth={snapshot.fl002_auth_error_count}"
        )

    def _parse_snapshot_date(self, raw_value):
        if raw_value:
            return date.fromisoformat(raw_value)
        return timezone.localdate()

    def _build_counts(self):
        status_rows = list(
            U001PipelineStatus.objects.values(
                'layer_id',
                'status',
                'last_error',
            )
        )
        rd001_rows = [row for row in status_rows if row['layer_id'] == 'RD-001']
        fl002_rows = [row for row in status_rows if row['layer_id'] == 'FL-002']

        rd001_buckets = Counter(
            _classify_error_bucket(row['last_error'])
            for row in rd001_rows
            if row.get('last_error')
        )
        fl002_buckets = Counter(
            _classify_error_bucket(row['last_error'])
            for row in fl002_rows
            if row.get('last_error')
        )

        return {
            'discovered_count': MigratedCoin.objects.count(),
            'mapped_count': PoolMapping.objects.values('coin_id').distinct().count(),
            'fl001_complete_count': U001PipelineStatus.objects.filter(
                layer_id='FL-001',
                status=PipelineCompleteness.WINDOW_COMPLETE,
            ).count(),
            'fl002_complete_count': U001PipelineStatus.objects.filter(
                layer_id='FL-002',
                status=PipelineCompleteness.WINDOW_COMPLETE,
            ).count(),
            'rd001_complete_count': U001PipelineStatus.objects.filter(
                layer_id='RD-001',
                status=PipelineCompleteness.WINDOW_COMPLETE,
            ).count(),
            'rd001_partial_count': U001PipelineStatus.objects.filter(
                layer_id='RD-001',
                status=PipelineCompleteness.PARTIAL,
            ).count(),
            'rd001_error_count': U001PipelineStatus.objects.filter(
                layer_id='RD-001',
                status=PipelineCompleteness.ERROR,
            ).count(),
            'rd001_transport_error_count': rd001_buckets.get('transport', 0),
            'rd001_guard_error_count': rd001_buckets.get('free_tier_guard', 0),
            'fl002_auth_error_count': fl002_buckets.get('auth', 0),
        }
