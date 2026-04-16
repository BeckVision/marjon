"""Run one policy-driven automation tick for U-001."""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pipeline.u001_automation import (
    ACTION_NAMES,
    collect_metrics,
    get_or_create_state,
    reset_daily_counters,
    select_next_action,
    snapshot_due,
)
from warehouse.models import U001AutomationTick


def _batch_result_is_total_failure(action, result_summary):
    if action not in {
        'rd001_recent',
        'rd001_partial_historical',
        'rd001_error_recovery',
        'rd001_guarded',
    }:
        return False
    if not isinstance(result_summary, dict):
        return False
    queued = result_summary.get('queued_coins') or 0
    failed = result_summary.get('failed') or 0
    succeeded = result_summary.get('succeeded') or 0
    records_loaded = result_summary.get('records_loaded') or 0
    return queued > 0 and succeeded == 0 and failed >= queued and records_loaded == 0


class Command(BaseCommand):
    help = "Run one automation tick for U-001"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Select and print the next action without executing it',
        )
        parser.add_argument(
            '--force-action',
            type=str,
            choices=ACTION_NAMES,
            default=None,
            help='Force a specific action for this tick',
        )
        parser.add_argument(
            '--skip-repair',
            action='store_true',
            help='Skip the stale-state repair pass at the start of the tick',
        )
        parser.add_argument(
            '--skip-snapshot',
            action='store_true',
            help='Skip the optional snapshot pass at the end of the tick',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        state = get_or_create_state()
        today = timezone.localdate(now)
        reset_daily_counters(state, today)
        repair_executed = not options['skip_repair']

        if options['dry_run']:
            decision = select_next_action(
                state,
                collect_metrics(now=now),
                force_action=options.get('force_action'),
            )
            self.stdout.write(f"selected_action: {decision.action}")
            self.stdout.write(f"reason: {decision.reason}")
            self.stdout.write(f"command: {decision.command or 'none'}")
            self.stdout.write(f"kwargs: {decision.kwargs}")
            self.stdout.write(
                f"snapshot_due: {snapshot_due(state, now=now) and not options['skip_snapshot']}"
            )
            return

        if not options['skip_repair']:
            call_command(
                'repair_u001_ingestion',
                stdout=self.stdout,
            )

        state.last_tick_at = now
        decision = select_next_action(
            state,
            collect_metrics(now=now),
            force_action=options.get('force_action'),
        )
        state.last_action = decision.action
        state.last_action_reason = decision.reason
        state.last_action_status = 'started'
        state.last_action_started_at = now
        state.last_action_completed_at = None
        state.notes = None
        state.save()
        tick = U001AutomationTick.objects.create(
            started_at=now,
            action=decision.action,
            reason=decision.reason,
            status='started',
            command=decision.command,
            command_kwargs=decision.kwargs,
            repaired_state=repair_executed,
        )

        self.stdout.write(f"selected_action: {decision.action}")
        self.stdout.write(f"reason: {decision.reason}")

        try:
            result_summary = {}
            if decision.command:
                command_result = call_command(
                    decision.command,
                    stdout=self.stdout,
                    **decision.kwargs,
                )
                if isinstance(command_result, dict):
                    result_summary = command_result
            if _batch_result_is_total_failure(decision.action, result_summary):
                raise RuntimeError(
                    f"{decision.action} queued {result_summary.get('queued_coins', 0)} coin(s) "
                    f"but all failed with 0 rows loaded"
                )

            state.last_action_status = 'complete'
            state.last_action_completed_at = timezone.now()
            state.consecutive_failures = 0

            if decision.action == 'rd001_error_recovery':
                state.error_lane_tick_counter = 0
            elif decision.action != 'no_action':
                state.error_lane_tick_counter += 1

            if decision.action == 'rd001_guarded':
                state.guarded_attempts_date = today
                state.guarded_attempts_today += 1

            state.save()
            tick.status = 'complete'
            tick.completed_at = state.last_action_completed_at
            tick.notes = state.notes
            tick.result_summary = result_summary
            tick.save(update_fields=['status', 'completed_at', 'notes', 'result_summary'])

        except Exception as exc:
            state.last_action_status = 'error'
            state.last_action_completed_at = timezone.now()
            state.consecutive_failures += 1
            state.notes = str(exc)
            if decision.action == 'rd001_guarded':
                state.guarded_attempts_date = today
                state.guarded_attempts_today += 1
            state.save()
            tick.status = 'error'
            tick.completed_at = state.last_action_completed_at
            tick.notes = str(exc)
            tick.save(update_fields=['status', 'completed_at', 'notes'])
            raise CommandError(
                f"U-001 automation action '{decision.action}' failed: {exc}"
            )

        if not options['skip_snapshot'] and snapshot_due(state, now=timezone.now()):
            call_command(
                'snapshot_u001_ops',
                stdout=self.stdout,
            )
            state.last_snapshot_date = timezone.localdate()
            state.save(update_fields=['last_snapshot_date', 'updated_at'])
            tick.snapshot_taken = True
            tick.save(update_fields=['snapshot_taken'])
