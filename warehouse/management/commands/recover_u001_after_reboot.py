"""Persist and execute one local post-reboot recovery run for U-001."""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from warehouse.models import U001BootRecoveryRun


class Command(BaseCommand):
    help = "Record and execute one post-reboot U-001 recovery run after DB is reachable"

    def add_arguments(self, parser):
        parser.add_argument(
            '--log-path',
            type=str,
            default=None,
            help='Optional path to the shell-side recovery log file',
        )

    def handle(self, *args, **options):
        started_at = timezone.now()
        run = U001BootRecoveryRun.objects.create(
            started_at=started_at,
            status='started',
            db_reachable=True,
            migrations_ok=True,
            log_path=options.get('log_path'),
        )

        self.stdout.write("recorded_boot_recovery: started")

        try:
            run.automation_tick_started = True
            run.save(update_fields=['automation_tick_started'])
            call_command(
                'automate_u001',
                stdout=self.stdout,
            )
            run.status = 'complete'
            run.automation_tick_status = 'complete'
            run.completed_at = timezone.now()
            run.save(update_fields=['status', 'automation_tick_status', 'completed_at'])
            self.stdout.write("recorded_boot_recovery: complete")
        except Exception as exc:
            run.status = 'error'
            run.automation_tick_status = 'error'
            run.completed_at = timezone.now()
            run.notes = str(exc)
            run.save(update_fields=['status', 'automation_tick_status', 'completed_at', 'notes'])
            raise CommandError(
                f"U-001 reboot recovery failed after DB startup: {exc}"
            )
