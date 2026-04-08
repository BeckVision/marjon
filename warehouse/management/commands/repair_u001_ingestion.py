"""Repair stale U-001 ingestion state left behind by interrupted runs."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from warehouse.models import (
    PipelineCompleteness,
    PipelineBatchRun,
    RunStatus,
    U001PipelineRun,
    U001PipelineStatus,
)


class Command(BaseCommand):
    help = "Mark stale U-001 batches, runs, and in-progress statuses as error so ingestion can resume"

    def add_arguments(self, parser):
        parser.add_argument(
            '--stale-hours',
            type=int,
            default=6,
            help='Treat started/in_progress rows older than this as stale (default: 6)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without modifying the database',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        stale_hours = options['stale_hours']
        cutoff = now - timedelta(hours=stale_hours)
        dry_run = options['dry_run']
        message = (
            f"Marked stale by repair_u001_ingestion at {now.isoformat()} "
            f"after exceeding {stale_hours}h without completion."
        )

        stale_batches = PipelineBatchRun.objects.filter(
            pipeline_id='U-001',
            status=RunStatus.STARTED,
            started_at__lt=cutoff,
        )
        stale_runs = U001PipelineRun.objects.filter(
            status=RunStatus.STARTED,
            started_at__lt=cutoff,
        )
        stale_statuses = U001PipelineStatus.objects.filter(
            status=PipelineCompleteness.IN_PROGRESS,
            last_run_at__lt=cutoff,
        )

        self.stdout.write(
            f"stale_batches={stale_batches.count()} "
            f"stale_runs={stale_runs.count()} "
            f"stale_statuses={stale_statuses.count()}"
        )

        if dry_run:
            self.stdout.write("Dry run only. No changes applied.")
            return

        batch_updates = stale_batches.update(
            status=RunStatus.ERROR,
            completed_at=now,
            error_message=message,
        )
        run_updates = stale_runs.update(
            status=RunStatus.ERROR,
            completed_at=now,
            error_message=message,
        )
        status_updates = stale_statuses.update(
            status=PipelineCompleteness.ERROR,
            last_error=message,
            updated_at=now,
        )

        self.stdout.write(
            f"updated_batches={batch_updates} "
            f"updated_runs={run_updates} "
            f"updated_statuses={status_updates}"
        )
