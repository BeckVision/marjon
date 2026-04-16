"""Report live U-001 ingestion freshness, coverage, and failure patterns."""

from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from django.utils import timezone

from warehouse.models import (
    HolderSnapshot,
    MigratedCoin,
    OHLCVCandle,
    PipelineBatchRun,
    PoolMapping,
    RawTransaction,
    U001PipelineRun,
    U001PipelineStatus,
)

FREE_TIER_GUARD_TEXT = 'exceeds free-tier guard'


class Command(BaseCommand):
    help = "Summarize U-001 ingestion freshness, coverage, and likely blockers"

    LAYERS = {
        'FL-001': OHLCVCandle,
        'FL-002': HolderSnapshot,
        'RD-001': RawTransaction,
    }

    def add_arguments(self, parser):
        parser.add_argument(
            '--stale-days',
            type=int,
            default=3,
            help='Warn when the latest U-001 ingestion is older than this many days (default: 3)',
        )
        parser.add_argument(
            '--stale-in-progress-hours',
            type=int,
            default=6,
            help='Warn when in_progress statuses are older than this many hours (default: 6)',
        )
        parser.add_argument(
            '--recent-runs',
            type=int,
            default=5,
            help='How many recent U-001 batch runs to show (default: 5)',
        )
        parser.add_argument(
            '--top-errors',
            type=int,
            default=5,
            help='How many common errors to show per layer (default: 5)',
        )
        parser.add_argument(
            '--fail-on-blockers',
            action='store_true',
            help='Exit non-zero when blockers are detected',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        stale_cutoff = now - timedelta(days=options['stale_days'])
        stale_in_progress_cutoff = now - timedelta(
            hours=options['stale_in_progress_hours']
        )

        mature_count = MigratedCoin.objects.filter(
            anchor_event__lte=now - MigratedCoin.OBSERVATION_WINDOW_END
        ).count()
        mapped_count = PoolMapping.objects.values('coin_id').distinct().count()

        latest_coin_anchor = MigratedCoin.objects.aggregate(v=Max('anchor_event'))['v']
        latest_coin_ingested = MigratedCoin.objects.aggregate(v=Max('ingested_at'))['v']

        self.stdout.write("=" * 60)
        self.stdout.write("U-001 INGESTION HEALTH")
        self.stdout.write("=" * 60)
        self.stdout.write(f"now_utc: {now.isoformat()}")
        self.stdout.write(f"coins_total: {MigratedCoin.objects.count()}")
        self.stdout.write(f"coins_mature: {mature_count}")
        self.stdout.write(f"coins_with_pool_mapping: {mapped_count}")
        self.stdout.write(f"latest_coin_anchor: {latest_coin_anchor}")
        self.stdout.write(f"latest_coin_ingested: {latest_coin_ingested}")

        blockers = []
        if latest_coin_ingested is None:
            blockers.append("No U-001 discovery data is present.")
        elif latest_coin_ingested < stale_cutoff:
            blockers.append(
                f"U-001 discovery is stale: latest coin ingestion is {latest_coin_ingested}."
            )

        self.stdout.write("\n--- Layers ---")
        for layer_id, model in self.LAYERS.items():
            latest_timestamp = model.objects.aggregate(v=Max('timestamp'))['v']
            latest_ingested = model.objects.aggregate(v=Max('ingested_at'))['v']
            data_coin_count = model.objects.values('coin_id').distinct().count()
            status_counts = self._status_counts(layer_id)

            self.stdout.write(f"\n{layer_id}")
            self.stdout.write(f"  latest_timestamp: {latest_timestamp}")
            self.stdout.write(f"  latest_ingested_at: {latest_ingested}")
            self.stdout.write(f"  coins_with_data: {data_coin_count}")
            self.stdout.write(
                "  status_counts: "
                + ", ".join(
                    f"{status}={count}" for status, count in status_counts.items()
                )
            )
            free_tier_guarded = self._free_tier_guarded_count(layer_id)
            if free_tier_guarded:
                self.stdout.write(
                    f"  free_tier_guarded_statuses: {free_tier_guarded}"
                )

            error_buckets = self._error_bucket_counts(layer_id)
            if error_buckets:
                self.stdout.write(
                    "  current_error_buckets: "
                    + ", ".join(
                        f"{bucket}={count}"
                        for bucket, count in error_buckets.items()
                    )
                )

            if latest_ingested is None:
                blockers.append(f"{layer_id} has no ingested data.")
            elif latest_ingested < stale_cutoff:
                blockers.append(
                    f"{layer_id} is stale: latest ingestion is {latest_ingested}."
                )

            stale_in_progress = U001PipelineStatus.objects.filter(
                layer_id=layer_id,
                status='in_progress',
                last_run_at__lt=stale_in_progress_cutoff,
            ).count()
            if stale_in_progress:
                blockers.append(
                    f"{layer_id} has {stale_in_progress} stale in_progress statuses older than "
                    f"{options['stale_in_progress_hours']}h."
                )
                self.stdout.write(
                    f"  stale_in_progress: {stale_in_progress}"
                )

            top_errors = self._top_errors(layer_id, options['top_errors'])
            if top_errors:
                self.stdout.write("  top_errors:")
                for count, message in top_errors:
                    self.stdout.write(f"    {count}x {message}")

                if error_buckets.get('auth'):
                    blockers.append(
                        f"{layer_id} is seeing authentication failures from its upstream provider."
                    )

        self.stdout.write("\n--- Recent U-001 Batches ---")
        recent_runs = PipelineBatchRun.objects.filter(
            pipeline_id='U-001'
        ).order_by('-started_at')[:options['recent_runs']]
        if recent_runs:
            for batch in recent_runs:
                self.stdout.write(
                    f"{batch.id} {batch.mode} {batch.status} "
                    f"started={batch.started_at.isoformat()} "
                    f"completed={batch.completed_at.isoformat() if batch.completed_at else None} "
                    f"succeeded={batch.coins_succeeded} failed={batch.coins_failed}"
                )
        else:
            self.stdout.write("No U-001 batch runs found.")

        stale_batches = PipelineBatchRun.objects.filter(
            pipeline_id='U-001',
            status='started',
            started_at__lt=stale_in_progress_cutoff,
        ).count()
        if stale_batches:
            blockers.append(
                f"U-001 has {stale_batches} stale batch runs still marked started."
            )

        unique_blockers = list(dict.fromkeys(blockers))
        if unique_blockers:
            self.stdout.write("\n--- Blockers ---")
            for item in unique_blockers:
                self.stdout.write(f"- {item}")
        else:
            self.stdout.write("\nNo blockers detected.")

        self.stdout.write("\n" + "=" * 60)

        if unique_blockers and options['fail_on_blockers']:
            raise CommandError("U-001 ingestion blockers detected")

    def _status_counts(self, layer_id):
        rows = (
            U001PipelineStatus.objects
            .filter(layer_id=layer_id)
            .values_list('status', flat=True)
        )
        counts = Counter(rows)
        ordered = {}
        for status in ('window_complete', 'partial', 'in_progress', 'error', 'not_started'):
            if counts.get(status):
                ordered[status] = counts[status]
        return ordered

    def _top_errors(self, layer_id, limit):
        rows = list(
            U001PipelineRun.objects.filter(
                layer_id=layer_id,
                status='error',
                error_message__isnull=False,
            ).values_list('error_message', flat=True)
        )
        if not rows:
            rows = list(
                U001PipelineStatus.objects.filter(
                    layer_id=layer_id,
                    last_error__isnull=False,
                ).values_list('last_error', flat=True)
            )

        counts = Counter((error or '').splitlines()[0][:220] for error in rows if error)
        return [(count, message) for message, count in counts.most_common(limit)]

    def _free_tier_guarded_count(self, layer_id):
        return U001PipelineStatus.objects.filter(
            layer_id=layer_id,
            last_error__icontains=FREE_TIER_GUARD_TEXT,
        ).count()

    def _error_bucket_counts(self, layer_id):
        rows = (
            U001PipelineStatus.objects
            .filter(layer_id=layer_id, last_error__isnull=False)
            .values_list('last_error', flat=True)
        )
        counts = Counter()
        for error in rows:
            bucket = self._classify_error_bucket(error)
            if bucket:
                counts[bucket] += 1

        ordered = {}
        for bucket in (
            'auth',
            'transport',
            'free_tier_guard',
            'expectation_failed',
            'rate_limited',
            'server_error',
            'response_validation',
            'json_decode',
            'other',
        ):
            if counts.get(bucket):
                ordered[bucket] = counts[bucket]
        return ordered

    def _classify_error_bucket(self, error):
        text = (error or '').lower()
        if not text:
            return None
        if '401 unauthorized' in text or '403 forbidden' in text:
            return 'auth'
        if FREE_TIER_GUARD_TEXT in text:
            return 'free_tier_guard'
        if (
            'transport_error' in text or
            'server disconnected' in text or
            'remoteprotocolerror' in text or
            'network error' in text
        ):
            return 'transport'
        if 'expectation failed' in text or 'expectation_failed_417' in text:
            return 'expectation_failed'
        if 'rate limited' in text or 'rate_limited_429' in text:
            return 'rate_limited'
        if 'server_error_' in text or 'server error ' in text:
            return 'server_error'
        if 'response_validation_error:' in text:
            return 'response_validation'
        if 'json_decode_error:' in text or 'json decode error' in text:
            return 'json_decode'
        return 'other'
