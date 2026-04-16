"""Audit U-001 unattended-safety conditions and fail on active blockers."""

from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pipeline.pipelines.rd001 import SHYFT_RETENTION_DAYS
from pipeline.u001_rd001_recent_runner import (
    last_successful_cycle_at,
    parse_runner_datetime,
    pid_alive,
    read_status_file,
    status_path,
)
from warehouse.models import (
    MigratedCoin,
    PipelineBatchRun,
    PipelineCompleteness,
    PoolMapping,
    U001AutomationState,
    U001AutomationTick,
    U001BootRecoveryRun,
    U001FL001DerivedAuditRun,
    U001OpsSnapshot,
    U001PipelineStatus,
    U001RD001ChainAuditRun,
    U001SourceAuditRun,
)

FREE_TIER_GUARD_TEXT = 'exceeds free-tier guard'
CONNECTIVITY_ERROR_PATTERNS = (
    'transport_error',
    'server disconnected',
    'remoteprotocolerror',
    'network error',
    'connection refused',
    'connect timeout',
    'connecttimeout',
    'timed out',
    'timeout',
    'temporary failure in name resolution',
    'name or service not known',
    'failed to establish a new connection',
    'getaddrinfo',
    'nodename nor servname provided',
    'dns',
)


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


def _result_count(tick, key):
    summary = getattr(tick, 'result_summary', None) or {}
    value = summary.get(key, 0)
    return value if isinstance(value, int) else 0


def _has_result_summary(tick):
    return bool(getattr(tick, 'result_summary', None))


def _step_count(tick, step_name, key):
    summary = getattr(tick, 'result_summary', None) or {}
    steps = summary.get('steps') or {}
    step = steps.get(step_name) or {}
    value = step.get(key, 0)
    return value if isinstance(value, int) else 0


def _latest_complete_streak(limit=20):
    ticks = list(
        U001AutomationTick.objects.filter(status='complete')
        .order_by('-started_at', '-id')[:limit]
    )
    if not ticks:
        return None, []

    action = ticks[0].action
    streak = []
    for tick in ticks:
        if tick.action != action:
            break
        streak.append(tick)
    return action, streak


def _looks_like_connectivity_issue(text):
    lowered = (text or '').lower()
    if not lowered:
        return False
    return any(pattern in lowered for pattern in CONNECTIVITY_ERROR_PATTERNS)


def _recent_connectivity_error_streak(limit=5):
    streak = []
    ticks = U001AutomationTick.objects.exclude(action='no_action').order_by('-started_at', '-id')[:limit]
    for tick in ticks:
        if tick.status != 'error' or not _looks_like_connectivity_issue(tick.notes):
            break
        streak.append(tick)
    return streak


def _latest_connectivity_issue_at(latest_tick, latest_boot_recovery, streak):
    candidates = []
    if streak:
        candidates.append(streak[0].completed_at or streak[0].started_at)
    elif latest_tick and latest_tick.status == 'error' and _looks_like_connectivity_issue(latest_tick.notes):
        candidates.append(latest_tick.completed_at or latest_tick.started_at)
    if (
        latest_boot_recovery
        and latest_boot_recovery.status == 'error'
        and _looks_like_connectivity_issue(latest_boot_recovery.notes)
    ):
        candidates.append(latest_boot_recovery.completed_at or latest_boot_recovery.started_at)
    return max(candidates) if candidates else None


def _automation_connectivity_summary(latest_tick, latest_boot_recovery, runner_raw):
    streak = _recent_connectivity_error_streak()
    latest_issue_at = _latest_connectivity_issue_at(latest_tick, latest_boot_recovery, streak)
    recovered_at = last_successful_cycle_at(runner_raw)
    summary = {
        'state': 'healthy',
        'streak_length': len(streak),
        'latest_action': latest_tick.action if latest_tick else None,
        'detail': 'No active connectivity signal.',
        'latest_issue_at': latest_issue_at,
        'recovered_at': recovered_at,
    }
    if recovered_at and latest_issue_at and recovered_at > latest_issue_at:
        summary.update({
            'state': 'recovered',
            'detail': 'Dedicated recent RD-001 runner completed a successful cycle after the latest connectivity failure.',
        })
        return summary
    if streak:
        summary.update({
            'state': 'critical' if len(streak) >= 2 else 'warn',
            'latest_action': streak[0].action,
            'detail': (
                f'{len(streak)} consecutive automation tick(s) failed with transport or reachability errors.'
            ),
        })
        return summary
    if latest_tick and latest_tick.status == 'error' and _looks_like_connectivity_issue(latest_tick.notes):
        summary.update({
            'state': 'warn',
            'detail': 'Latest automation tick failed with a transport or reachability error.',
        })
        return summary
    if (
        latest_boot_recovery
        and latest_boot_recovery.status == 'error'
        and _looks_like_connectivity_issue(latest_boot_recovery.notes)
    ):
        summary.update({
            'state': 'warn',
            'detail': 'Latest boot recovery failed with a transport or reachability error.',
        })
    return summary


def _empty_truth_audit_day(day):
    return {
        'date': day,
        'source_audit_runs': 0,
        'source_audit_findings': 0,
        'source_audit_warnings': 0,
        'source_audit_error_runs': 0,
        'rd001_chain_audit_runs': 0,
        'rd001_chain_audit_findings': 0,
        'rd001_chain_audit_warnings': 0,
        'rd001_chain_audit_error_runs': 0,
        'fl001_derived_audit_runs': 0,
        'fl001_derived_audit_findings': 0,
        'fl001_derived_audit_warnings': 0,
        'fl001_derived_audit_error_runs': 0,
    }


def _apply_truth_audit_run(row_by_date, run, prefix):
    row = row_by_date.get(run.started_at.date())
    if not row:
        return
    row[f'{prefix}_runs'] += 1
    row[f'{prefix}_findings'] += run.finding_count
    row[f'{prefix}_warnings'] += run.warning_count
    if run.status == 'error':
        row[f'{prefix}_error_runs'] += 1


def _recent_truth_audit_summary(now, days):
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    daily_rows = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        daily_rows.append(_empty_truth_audit_day(day))
    row_by_date = {row['date']: row for row in daily_rows}

    for run in U001SourceAuditRun.objects.filter(started_at__gte=start).order_by('started_at'):
        _apply_truth_audit_run(row_by_date, run, 'source_audit')
    for run in U001RD001ChainAuditRun.objects.filter(started_at__gte=start).order_by('started_at'):
        _apply_truth_audit_run(row_by_date, run, 'rd001_chain_audit')
    for run in U001FL001DerivedAuditRun.objects.filter(started_at__gte=start).order_by('started_at'):
        _apply_truth_audit_run(row_by_date, run, 'fl001_derived_audit')

    days_with_any = 0
    days_with_full = 0
    days_with_findings = 0
    days_with_warnings = 0
    for row in daily_rows:
        source_runs = row['source_audit_runs']
        chain_runs = row['rd001_chain_audit_runs']
        derived_runs = row['fl001_derived_audit_runs']
        if source_runs or chain_runs or derived_runs:
            days_with_any += 1
        if source_runs and chain_runs and derived_runs:
            days_with_full += 1
        if (
            row['source_audit_findings']
            or row['rd001_chain_audit_findings']
            or row['fl001_derived_audit_findings']
        ):
            days_with_findings += 1
        if (
            row['source_audit_warnings']
            or row['rd001_chain_audit_warnings']
            or row['fl001_derived_audit_warnings']
            or row['source_audit_error_runs']
            or row['rd001_chain_audit_error_runs']
            or row['fl001_derived_audit_error_runs']
        ):
            days_with_warnings += 1

    return {
        'days': days,
        'days_with_any': days_with_any,
        'days_without_any': max(days - days_with_any, 0),
        'days_with_full': days_with_full,
        'days_with_findings': days_with_findings,
        'days_with_warnings': days_with_warnings,
    }


class Command(BaseCommand):
    help = "Audit whether U-001 is safe to leave unattended under the current scheduler and coverage state"

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-discovery-stale-hours',
            type=int,
            default=36,
            help='Block if latest discovery ingestion is older than this many hours (default: 36)',
        )
        parser.add_argument(
            '--max-automation-stale-hours',
            type=int,
            default=2,
            help='Block if no automation tick has started within this many hours (default: 2)',
        )
        parser.add_argument(
            '--max-rd001-recent-runner-stale-minutes',
            type=int,
            default=15,
            help='Warn if the dedicated recent RD-001 runner heartbeat is older than this many minutes (default: 15)',
        )
        parser.add_argument(
            '--max-snapshot-stale-days',
            type=int,
            default=2,
            help='Block if the latest U-001 ops snapshot is older than this many days (default: 2)',
        )
        parser.add_argument(
            '--max-source-audit-stale-days',
            type=int,
            default=2,
            help='Warn if the latest live source audit is older than this many days (default: 2)',
        )
        parser.add_argument(
            '--max-rd001-chain-audit-stale-days',
            type=int,
            default=2,
            help='Warn if the latest RD-001 direct chain audit is older than this many days (default: 2)',
        )
        parser.add_argument(
            '--max-fl001-derived-audit-stale-days',
            type=int,
            default=2,
            help='Warn if the latest FL-001 derived audit is older than this many days (default: 2)',
        )
        parser.add_argument(
            '--truth-audit-window-days',
            type=int,
            default=7,
            help='Evaluate recent Phase 0 truth-audit coverage over this many trailing days (default: 7)',
        )
        parser.add_argument(
            '--min-truth-audit-days-with-any',
            type=int,
            default=1,
            help='Warn if fewer than this many days in the truth-audit window had any truth-audit activity (default: 1)',
        )
        parser.add_argument(
            '--max-consecutive-failures',
            type=int,
            default=3,
            help='Block if the controller state has at least this many consecutive failures (default: 3)',
        )
        parser.add_argument(
            '--min-fl002-complete-pct',
            type=float,
            default=0.80,
            help='Warn when FL-002 mature coverage falls below this fraction (default: 0.80)',
        )
        parser.add_argument(
            '--min-rd001-complete-pct',
            type=float,
            default=0.80,
            help='Warn when RD-001 mature mapped coverage falls below this fraction (default: 0.80)',
        )
        parser.add_argument(
            '--max-rd001-transport-statuses',
            type=int,
            default=25,
            help='Warn when current RD-001 transport statuses exceed this count (default: 25)',
        )
        parser.add_argument(
            '--min-recent-mapped-pct',
            type=float,
            default=0.05,
            help='Warn when recent discovered coins mapped inside the Shyft retention window fall below this fraction (default: 0.05)',
        )
        parser.add_argument(
            '--max-single-lane-streak',
            type=int,
            default=4,
            help='Warn when the latest complete automation lane has repeated this many times in a row (default: 4)',
        )
        parser.add_argument(
            '--rd001-no-progress-ticks',
            type=int,
            default=3,
            help='Warn when this many consecutive complete rd001_recent ticks load zero rows (default: 3)',
        )
        parser.add_argument(
            '--fail-on-warnings',
            action='store_true',
            help='Exit non-zero on warnings as well as blockers',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        today = timezone.localdate(now)

        discovery_cutoff = now - timedelta(hours=options['max_discovery_stale_hours'])
        automation_cutoff = now - timedelta(hours=options['max_automation_stale_hours'])
        rd001_recent_runner_cutoff = now - timedelta(minutes=options['max_rd001_recent_runner_stale_minutes'])
        snapshot_cutoff = today - timedelta(days=options['max_snapshot_stale_days'])
        source_audit_cutoff = now - timedelta(days=options['max_source_audit_stale_days'])
        rd001_chain_audit_cutoff = now - timedelta(days=options['max_rd001_chain_audit_stale_days'])
        fl001_derived_audit_cutoff = now - timedelta(days=options['max_fl001_derived_audit_stale_days'])

        latest_coin_ingested = (
            MigratedCoin.objects.order_by('-ingested_at')
            .values_list('ingested_at', flat=True)
            .first()
        )
        latest_discovery_batch = (
            PipelineBatchRun.objects.filter(
                pipeline_id='universe',
                status='complete',
            ).order_by('-completed_at', '-started_at')
            .first()
        )
        automation_state = U001AutomationState.objects.filter(singleton_key='u001').first()
        latest_tick = U001AutomationTick.objects.order_by('-started_at', '-id').first()
        latest_complete_tick = (
            U001AutomationTick.objects.filter(status='complete')
            .order_by('-started_at', '-id')
            .first()
        )
        latest_complete_action, latest_complete_streak = _latest_complete_streak()
        latest_refresh_core_tick = (
            U001AutomationTick.objects.filter(
                action='refresh_core',
                status='complete',
            ).order_by('-started_at', '-id')
            .first()
        )
        latest_boot_recovery = U001BootRecoveryRun.objects.order_by('-started_at', '-id').first()
        latest_snapshot = U001OpsSnapshot.objects.order_by('-snapshot_date').first()
        latest_source_audit = U001SourceAuditRun.objects.order_by('-started_at', '-id').first()
        latest_rd001_chain_audit = U001RD001ChainAuditRun.objects.order_by('-started_at', '-id').first()
        latest_fl001_derived_audit = U001FL001DerivedAuditRun.objects.order_by('-started_at', '-id').first()
        runner_path = status_path()
        runner_raw = read_status_file(runner_path)
        runner_updated_at = (
            parse_runner_datetime(runner_raw.get('updated_at'))
            if runner_raw and not runner_raw.get('_error')
            else None
        )
        runner_pid = runner_raw.get('pid') if runner_raw and not runner_raw.get('_error') else None
        runner_pid_alive = pid_alive(runner_pid) if runner_pid else False
        runner_state = runner_raw.get('state') if runner_raw and not runner_raw.get('_error') else None
        runner_cycle = runner_raw.get('cycle') if runner_raw and not runner_raw.get('_error') else None
        connectivity_summary = _automation_connectivity_summary(
            latest_tick=latest_tick,
            latest_boot_recovery=latest_boot_recovery,
            runner_raw=runner_raw,
        )
        truth_audit_summary = _recent_truth_audit_summary(
            now=now,
            days=options['truth_audit_window_days'],
        )
        discovery_activity_candidates = [
            latest_coin_ingested,
            latest_discovery_batch.completed_at if latest_discovery_batch else None,
            latest_refresh_core_tick.completed_at if latest_refresh_core_tick else None,
        ]
        latest_discovery_activity = max(
            (value for value in discovery_activity_candidates if value is not None),
            default=None,
        )

        mature_qs = MigratedCoin.objects.filter(
            anchor_event__lte=now - MigratedCoin.OBSERVATION_WINDOW_END,
        )
        recent_qs = MigratedCoin.objects.filter(
            anchor_event__gte=now - timedelta(days=SHYFT_RETENTION_DAYS),
        )
        mature_count = mature_qs.count()
        recent_count = recent_qs.count()
        mature_mapped_count = PoolMapping.objects.filter(
            coin__anchor_event__lte=now - MigratedCoin.OBSERVATION_WINDOW_END,
        ).values('coin_id').distinct().count()
        recent_mapped_count = PoolMapping.objects.filter(
            coin__anchor_event__gte=now - timedelta(days=SHYFT_RETENTION_DAYS),
        ).values('coin_id').distinct().count()

        fl002_complete_count = U001PipelineStatus.objects.filter(
            layer_id='FL-002',
            status=PipelineCompleteness.WINDOW_COMPLETE,
            coin__anchor_event__lte=now - MigratedCoin.OBSERVATION_WINDOW_END,
        ).count()
        rd001_complete_count = U001PipelineStatus.objects.filter(
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
            coin__anchor_event__lte=now - MigratedCoin.OBSERVATION_WINDOW_END,
            coin_id__in=PoolMapping.objects.values_list('coin_id', flat=True),
        ).count()

        fl002_complete_pct = (
            fl002_complete_count / mature_count if mature_count else 1.0
        )
        rd001_complete_pct = (
            rd001_complete_count / mature_mapped_count if mature_mapped_count else 1.0
        )
        recent_mapped_pct = (
            recent_mapped_count / recent_count if recent_count else 1.0
        )
        recent_mapped_label = (
            f'{recent_mapped_pct:.1%}' if recent_count else 'n/a'
        )

        fl002_auth_count = self._bucket_count('FL-002', 'auth')
        rd001_transport_count = self._bucket_count('RD-001', 'transport')
        rd001_guard_count = self._bucket_count('RD-001', 'free_tier_guard')
        streak_structured_count = sum(
            1 for tick in latest_complete_streak if _has_result_summary(tick)
        )
        streak_loaded = sum(
            _result_count(tick, 'records_loaded')
            for tick in latest_complete_streak
        )
        streak_holders_loaded = sum(
            _step_count(tick, 'holders', 'records_loaded')
            for tick in latest_complete_streak
        )
        streak_holders_succeeded = sum(
            _step_count(tick, 'holders', 'succeeded')
            for tick in latest_complete_streak
        )
        streak_pool_mapping_mapped = sum(
            _step_count(tick, 'pool_mapping', 'mapped')
            for tick in latest_complete_streak
        )
        streak_loaded_label = (
            str(streak_loaded)
            if latest_complete_streak and streak_structured_count == len(latest_complete_streak)
            else 'n/a'
        )

        blockers = []
        warnings = []

        if latest_discovery_activity is None:
            blockers.append('Discovery has never ingested any U-001 coins.')
        elif latest_discovery_activity < discovery_cutoff:
            blockers.append(
                f'Discovery is stale: latest refresh evidence is {latest_discovery_activity}.'
            )

        if latest_tick is None:
            blockers.append('Automation has no persisted non-dry-run ticks yet.')
        elif latest_tick.started_at < automation_cutoff:
            blockers.append(
                f'Automation is stale: latest tick started at {latest_tick.started_at}.'
            )

        if latest_complete_tick is None:
            blockers.append('Automation has no successful ticks yet.')

        if latest_snapshot is None:
            blockers.append('No U-001 ops snapshot has been recorded yet.')
        elif latest_snapshot.snapshot_date < snapshot_cutoff:
            blockers.append(
                f'U-001 ops snapshot is stale: latest snapshot is {latest_snapshot.snapshot_date}.'
            )

        if (
            automation_state
            and automation_state.consecutive_failures >= options['max_consecutive_failures']
        ):
            blockers.append(
                'Automation controller has entered repeated-failure territory: '
                f'{automation_state.consecutive_failures} consecutive failures.'
            )

        if fl002_auth_count:
            blockers.append(
                f'FL-002 has {fl002_auth_count} current auth-failure statuses.'
            )

        boot_recovery_at = (
            latest_boot_recovery.completed_at or latest_boot_recovery.started_at
            if latest_boot_recovery else None
        )
        if (
            latest_boot_recovery
            and latest_boot_recovery.status == 'error'
            and not (
                connectivity_summary.get('recovered_at')
                and boot_recovery_at
                and connectivity_summary['recovered_at'] > boot_recovery_at
            )
        ):
            warnings.append(
                'Latest reboot recovery failed after DB startup: '
                f'{latest_boot_recovery.notes or "see recovery log for details."}'
            )
        if runner_raw is None:
            warnings.append(
                'Dedicated recent RD-001 runner has no heartbeat file yet.'
            )
        elif runner_raw.get('_error'):
            warnings.append(
                'Dedicated recent RD-001 runner heartbeat could not be read: '
                f"{runner_raw['_error']}"
            )
        else:
            if not runner_pid_alive:
                warnings.append(
                    'Dedicated recent RD-001 runner heartbeat exists, but the process is not alive.'
                )
            if runner_updated_at and runner_updated_at < rd001_recent_runner_cutoff:
                warnings.append(
                    'Dedicated recent RD-001 runner heartbeat is stale: '
                    f'latest update was at {runner_updated_at}.'
                )
            if runner_state == 'cycle_error':
                warnings.append(
                    'Dedicated recent RD-001 runner is alive, but its latest cycle failed and it is backing off.'
                )
        if connectivity_summary['state'] in {'warn', 'critical'}:
            warnings.append(
                'Automation currently looks blocked by internet or upstream reachability problems: '
                f"{connectivity_summary['detail']}"
            )

        if truth_audit_summary['days_with_any'] < options['min_truth_audit_days_with_any']:
            warnings.append(
                'Recent Phase 0 truth-audit coverage is too thin: '
                f"{truth_audit_summary['days_with_any']}/{truth_audit_summary['days']} "
                'days had any truth-audit activity.'
            )

        if fl002_complete_pct < options['min_fl002_complete_pct']:
            warnings.append(
                f'FL-002 mature coverage is only {fl002_complete_pct:.1%} '
                f'({fl002_complete_count}/{mature_count}).'
            )

        if rd001_complete_pct < options['min_rd001_complete_pct']:
            warnings.append(
                f'RD-001 mature mapped coverage is only {rd001_complete_pct:.1%} '
                f'({rd001_complete_count}/{mature_mapped_count}).'
            )

        if rd001_transport_count > options['max_rd001_transport_statuses']:
            warnings.append(
                f'RD-001 transport residue remains high at {rd001_transport_count} current statuses.'
            )

        if rd001_guard_count:
            warnings.append(
                f'RD-001 still has {rd001_guard_count} free-tier-guarded statuses parked.'
            )

        if recent_mapped_pct < options['min_recent_mapped_pct']:
            warnings.append(
                f'Recent pool mapping coverage inside the {SHYFT_RETENTION_DAYS}-day Shyft window '
                f'is only {recent_mapped_pct:.1%} ({recent_mapped_count}/{recent_count}).'
            )

        if latest_complete_action and len(latest_complete_streak) >= options['max_single_lane_streak']:
            if latest_complete_action == 'pool_mapping_recent':
                if streak_structured_count == len(latest_complete_streak):
                    warnings.append(
                        f'Automation has completed {len(latest_complete_streak)} consecutive '
                        'pool_mapping_recent ticks with '
                        f'{streak_pool_mapping_mapped} total mapped coins across the streak, '
                        f'while recent mapping is still only {recent_mapped_pct:.1%} '
                        f'({recent_mapped_count}/{recent_count}).'
                    )
                else:
                    warnings.append(
                        f'Automation has completed {len(latest_complete_streak)} consecutive '
                        'pool_mapping_recent ticks, but structured yield summaries are not available for '
                        f'{len(latest_complete_streak) - streak_structured_count} older tick(s).'
                    )
            elif latest_complete_action == 'holders_catchup':
                if streak_structured_count == len(latest_complete_streak):
                    warnings.append(
                        f'Automation has completed {len(latest_complete_streak)} consecutive '
                        'holders_catchup ticks with '
                        f'{streak_holders_loaded} loaded holder rows across the streak, '
                        f'while FL-002 mature coverage is still only {fl002_complete_pct:.1%} '
                        f'({fl002_complete_count}/{mature_count}).'
                    )
                else:
                    warnings.append(
                        f'Automation has completed {len(latest_complete_streak)} consecutive '
                        'holders_catchup ticks, but structured yield summaries are not available for '
                        f'{len(latest_complete_streak) - streak_structured_count} older tick(s).'
                    )
            elif latest_complete_action == 'rd001_recent':
                if streak_structured_count == len(latest_complete_streak):
                    warnings.append(
                        f'Automation has completed {len(latest_complete_streak)} consecutive '
                        f'rd001_recent ticks with {streak_loaded} total loaded rows across the streak.'
                    )
                else:
                    warnings.append(
                        f'Automation has completed {len(latest_complete_streak)} consecutive '
                        'rd001_recent ticks, but structured yield summaries are not available for '
                        f'{len(latest_complete_streak) - streak_structured_count} older tick(s).'
                    )

        if (
            latest_complete_action == 'rd001_recent'
            and len(latest_complete_streak) >= options['rd001_no_progress_ticks']
            and streak_structured_count == len(latest_complete_streak)
            and streak_loaded == 0
        ):
            warnings.append(
                f'Automation completed {len(latest_complete_streak)} consecutive rd001_recent ticks '
                'without loading any rows.'
            )
        if (
            latest_complete_action == 'holders_catchup'
            and len(latest_complete_streak) >= options['max_single_lane_streak']
            and streak_structured_count == len(latest_complete_streak)
            and streak_holders_loaded == 0
            and streak_holders_succeeded == 0
        ):
            warnings.append(
                f'Automation completed {len(latest_complete_streak)} consecutive holders_catchup ticks '
                'without loading any holder rows.'
            )
        if (
            latest_complete_action == 'pool_mapping_recent'
            and len(latest_complete_streak) >= options['max_single_lane_streak']
            and streak_structured_count == len(latest_complete_streak)
            and streak_pool_mapping_mapped == 0
        ):
            warnings.append(
                f'Automation completed {len(latest_complete_streak)} consecutive pool_mapping_recent ticks '
                'without mapping any recent coins.'
            )

        if latest_source_audit is None:
            warnings.append(
                'No live source audit has been recorded yet.'
            )
        else:
            latest_source_audit_at = latest_source_audit.completed_at or latest_source_audit.started_at
            if latest_source_audit_at < source_audit_cutoff:
                warnings.append(
                    f'Live source audit is stale: latest run completed at {latest_source_audit_at}.'
                )
            if latest_source_audit.status == 'error':
                warnings.append(
                    'Latest live source audit failed to execute cleanly.'
                )
            elif latest_source_audit.finding_count:
                warnings.extend(self._latest_source_messages(latest_source_audit, 'findings'))
            elif latest_source_audit.warning_count:
                warnings.extend(self._latest_source_messages(latest_source_audit, 'warnings'))

        if latest_rd001_chain_audit is None:
            warnings.append(
                'No RD-001 direct chain audit has been recorded yet.'
            )
        else:
            latest_rd001_chain_audit_at = (
                latest_rd001_chain_audit.completed_at or latest_rd001_chain_audit.started_at
            )
            latest_rd001_chain_options = latest_rd001_chain_audit.options or {}
            if latest_rd001_chain_audit_at < rd001_chain_audit_cutoff:
                warnings.append(
                    'RD-001 direct chain audit is stale: '
                    f'latest run completed at {latest_rd001_chain_audit_at}.'
                )
            if latest_rd001_chain_options.get('rpc_source') == 'public_fallback':
                warnings.append(
                    'Latest RD-001 direct chain audit used the public Solana RPC fallback; '
                    'configure a dedicated keyed RPC URL for more reliable window reconciliation.'
                )
            if latest_rd001_chain_audit.status == 'error':
                warnings.append(
                    'Latest RD-001 direct chain audit failed to execute cleanly.'
                )
            elif latest_rd001_chain_audit.finding_count:
                warnings.extend(
                    self._latest_run_messages(
                        latest_rd001_chain_audit,
                        'findings',
                        prefix='Latest RD-001 direct chain audit',
                    )
                )

        if latest_fl001_derived_audit is None:
            warnings.append(
                'No FL-001 derived audit has been recorded yet.'
            )
        else:
            latest_fl001_derived_audit_at = (
                latest_fl001_derived_audit.completed_at or latest_fl001_derived_audit.started_at
            )
            if latest_fl001_derived_audit_at < fl001_derived_audit_cutoff:
                warnings.append(
                    'FL-001 derived audit is stale: '
                    f'latest run completed at {latest_fl001_derived_audit_at}.'
                )
            if latest_fl001_derived_audit.status == 'error':
                warnings.append(
                    'Latest FL-001 derived audit failed to execute cleanly.'
                )
            elif latest_fl001_derived_audit.finding_count:
                warnings.extend(
                    self._latest_run_messages(
                        latest_fl001_derived_audit,
                        'findings',
                        prefix='Latest FL-001 derived audit',
                    )
                )
            elif latest_fl001_derived_audit.warning_count:
                warnings.extend(
                    self._latest_run_messages(
                        latest_fl001_derived_audit,
                        'warnings',
                        prefix='Latest FL-001 derived audit',
                    )
                )
            elif latest_rd001_chain_audit.warning_count:
                warnings.extend(
                    self._latest_run_messages(
                        latest_rd001_chain_audit,
                        'warnings',
                        prefix='Latest RD-001 direct chain audit',
                    )
                )

        self.stdout.write("=" * 60)
        self.stdout.write("U-001 UNATTENDED SAFETY AUDIT")
        self.stdout.write("=" * 60)
        self.stdout.write(f"now_utc: {now.isoformat()}")
        self.stdout.write(f"latest_coin_ingested: {latest_coin_ingested}")
        self.stdout.write(f"latest_discovery_activity: {latest_discovery_activity}")
        self.stdout.write(
            f"latest_automation_tick: {latest_tick.started_at if latest_tick else None}"
        )
        self.stdout.write(
            f"latest_successful_tick: {latest_complete_tick.started_at if latest_complete_tick else None}"
        )
        self.stdout.write(
            "latest_boot_recovery: "
            f"{latest_boot_recovery.completed_at or latest_boot_recovery.started_at if latest_boot_recovery else None}"
        )
        self.stdout.write(
            f"latest_boot_recovery_status: {latest_boot_recovery.status if latest_boot_recovery else None}"
        )
        self.stdout.write(
            "latest_boot_recovery_tick_status: "
            f"{latest_boot_recovery.automation_tick_status if latest_boot_recovery else None}"
        )
        self.stdout.write(
            f"rd001_recent_runner_status_file: {runner_path}"
        )
        self.stdout.write(
            f"rd001_recent_runner_state: {runner_state}"
        )
        self.stdout.write(
            f"rd001_recent_runner_pid: {runner_pid}"
        )
        self.stdout.write(
            f"rd001_recent_runner_pid_alive: {runner_pid_alive}"
        )
        self.stdout.write(
            f"rd001_recent_runner_updated_at: {runner_updated_at}"
        )
        self.stdout.write(
            f"rd001_recent_runner_cycle: {runner_cycle}"
        )
        self.stdout.write(
            f"automation_connectivity_state: {connectivity_summary['state']}"
        )
        self.stdout.write(
            f"automation_connectivity_streak: {connectivity_summary['streak_length']}"
        )
        self.stdout.write(
            f"automation_connectivity_action: {connectivity_summary['latest_action']}"
        )
        self.stdout.write(
            f"automation_connectivity_latest_issue_at: {connectivity_summary['latest_issue_at']}"
        )
        self.stdout.write(
            f"automation_connectivity_recovered_at: {connectivity_summary['recovered_at']}"
        )
        self.stdout.write(f"latest_complete_action: {latest_complete_action}")
        self.stdout.write(f"latest_complete_streak: {len(latest_complete_streak)}")
        self.stdout.write(
            f"latest_complete_streak_structured_ticks: {streak_structured_count}/{len(latest_complete_streak)}"
        )
        self.stdout.write(f"latest_complete_streak_loaded_rows: {streak_loaded_label}")
        self.stdout.write(
            f"latest_snapshot_date: {latest_snapshot.snapshot_date if latest_snapshot else None}"
        )
        self.stdout.write(
            "latest_source_audit: "
            f"{(latest_source_audit.completed_at or latest_source_audit.started_at) if latest_source_audit else None}"
        )
        self.stdout.write(
            "latest_rd001_chain_audit: "
            f"{(latest_rd001_chain_audit.completed_at or latest_rd001_chain_audit.started_at) if latest_rd001_chain_audit else None}"
        )
        self.stdout.write(
            "latest_rd001_chain_audit_status: "
            f"{latest_rd001_chain_audit.status if latest_rd001_chain_audit else None}"
        )
        self.stdout.write(
            "latest_fl001_derived_audit: "
            f"{(latest_fl001_derived_audit.completed_at or latest_fl001_derived_audit.started_at) if latest_fl001_derived_audit else None}"
        )
        self.stdout.write(
            "latest_fl001_derived_audit_status: "
            f"{latest_fl001_derived_audit.status if latest_fl001_derived_audit else None}"
        )
        self.stdout.write(
            f"recent_truth_audit_window_days: {truth_audit_summary['days']}"
        )
        self.stdout.write(
            "recent_truth_audit_days_with_any: "
            f"{truth_audit_summary['days_with_any']}"
        )
        self.stdout.write(
            "recent_truth_audit_days_without_any: "
            f"{truth_audit_summary['days_without_any']}"
        )
        self.stdout.write(
            "recent_truth_audit_days_with_full: "
            f"{truth_audit_summary['days_with_full']}"
        )
        self.stdout.write(
            "recent_truth_audit_days_with_findings: "
            f"{truth_audit_summary['days_with_findings']}"
        )
        self.stdout.write(
            "recent_truth_audit_days_with_warnings: "
            f"{truth_audit_summary['days_with_warnings']}"
        )
        self.stdout.write(
            f"fl002_mature_complete_pct: {fl002_complete_pct:.1%} "
            f"({fl002_complete_count}/{mature_count})"
        )
        self.stdout.write(
            f"rd001_mature_mapped_complete_pct: {rd001_complete_pct:.1%} "
            f"({rd001_complete_count}/{mature_mapped_count})"
        )
        self.stdout.write(
            f"recent_mapped_pct_{SHYFT_RETENTION_DAYS}d: {recent_mapped_label} "
            f"({recent_mapped_count}/{recent_count})"
        )
        self.stdout.write(f"fl002_auth_statuses: {fl002_auth_count}")
        self.stdout.write(f"rd001_transport_statuses: {rd001_transport_count}")
        self.stdout.write(f"rd001_guarded_statuses: {rd001_guard_count}")
        self.stdout.write(
            "external_truth_check: sampled live-source audit, sampled RD-001 direct chain audit, and sampled FL-001 derived audit available "
            "(latest results are evaluated here when present)."
        )

        if blockers:
            self.stdout.write("\n--- Blockers ---")
            for item in blockers:
                self.stdout.write(f"- {item}")
        else:
            self.stdout.write("\nNo blockers detected.")

        if warnings:
            self.stdout.write("\n--- Warnings ---")
            for item in warnings:
                self.stdout.write(f"- {item}")
        else:
            self.stdout.write("\nNo warnings detected.")

        self.stdout.write("\n" + "=" * 60)

        if blockers or (warnings and options['fail_on_warnings']):
            raise CommandError("U-001 unattended safety audit failed")

    def _bucket_count(self, layer_id, bucket):
        rows = (
            U001PipelineStatus.objects.filter(
                layer_id=layer_id,
                last_error__isnull=False,
            ).values_list('last_error', flat=True)
        )
        counts = Counter(
            _classify_error_bucket(error)
            for error in rows
            if error
        )
        return counts.get(bucket, 0)

    def _latest_source_messages(self, source_audit, key):
        return self._latest_run_messages(
            source_audit,
            key,
            prefix='Latest live source audit',
        )

    def _latest_run_messages(self, run, key, prefix):
        summary = run.summary or {}
        messages = summary.get(key) or []
        if messages:
            return [f'{prefix} {key[:-1]}: {messages[0]}']
        count = run.finding_count if key == 'findings' else run.warning_count
        return [f'{prefix} reported {count} {key}.']
