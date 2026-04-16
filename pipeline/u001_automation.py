"""Policy helpers for one-tick U-001 automation."""

from datetime import timedelta
from dataclasses import dataclass
import os

from django.utils import timezone

from pipeline.management.commands.fetch_transactions import SHYFT_RETENTION_DAYS
from pipeline.u001_rd001_recent_runner import (
    parse_runner_datetime,
    pid_alive,
    read_status_file,
)
from warehouse.models import (
    MigratedCoin,
    PoolMapping,
    RawTransaction,
    U001AutomationState,
    U001AutomationTick,
    U001FL001DerivedAuditRun,
    U001PipelineStatus,
    U001RD001ChainAuditRun,
    U001SourceAuditRun,
)

FREE_TIER_GUARD_TEXT = 'exceeds free-tier guard'
AUTH_ERROR_PATTERNS = (
    '401 unauthorized',
    '403 forbidden',
)
TRANSPORT_ERROR_PATTERNS = (
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
RECENT_COHORT_SIZE = 1000
ACTION_NAMES = (
    'refresh_core',
    'truth_source_audit',
    'pool_mapping_recent',
    'holders_catchup',
    'rd001_recent',
    'truth_rd001_chain_audit',
    'truth_fl001_derived_audit',
    'rd001_partial_historical',
    'rd001_error_recovery',
    'rd001_guarded',
    'no_action',
)


@dataclass(frozen=True)
class AutomationDecision:
    action: str
    reason: str
    command: str | None
    kwargs: dict


def _env_int(name, default):
    value = os.environ.get(name)
    return int(value) if value else default


def _env_float(name, default):
    value = os.environ.get(name)
    return float(value) if value else default


def get_or_create_state():
    """Return the singleton controller state row for U-001."""
    state, _ = U001AutomationState.objects.get_or_create(singleton_key='u001')
    return state


def reset_daily_counters(state, today):
    """Reset per-day counters when the local date rolls over."""
    if state.guarded_attempts_date != today:
        state.guarded_attempts_date = today
        state.guarded_attempts_today = 0


def policy_config():
    """Resolve automation policy config from the environment."""
    return {
        'discovery_stale_hours': _env_int(
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS',
            24,
        ),
        'core_cooldown_hours': _env_int(
            'MARJON_U001_AUTOMATION_CORE_COOLDOWN_HOURS',
            6,
        ),
        'pool_mapping_recent_cooldown_hours': _env_float(
            'MARJON_U001_AUTOMATION_POOL_MAPPING_RECENT_COOLDOWN_HOURS',
            0.5,
        ),
        'truth_source_audit_cooldown_hours': _env_float(
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS',
            24,
        ),
        'holders_cooldown_hours': _env_int(
            'MARJON_U001_AUTOMATION_HOLDERS_COOLDOWN_HOURS',
            6,
        ),
        'holders_auth_pause_hours': _env_int(
            'MARJON_U001_AUTOMATION_HOLDERS_AUTH_PAUSE_HOURS',
            6,
        ),
        'holders_no_progress_after_ticks': _env_int(
            'MARJON_U001_AUTOMATION_HOLDERS_NO_PROGRESS_AFTER_TICKS',
            4,
        ),
        'holders_no_progress_pause_hours': _env_float(
            'MARJON_U001_AUTOMATION_HOLDERS_NO_PROGRESS_PAUSE_HOURS',
            6,
        ),
        'recent_mapping_min_pct': _env_float(
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT',
            0.25,
        ),
        'fl002_min_complete_pct': _env_float(
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT',
            0.25,
        ),
        'error_every_n_ticks': _env_int(
            'MARJON_U001_AUTOMATION_ERROR_EVERY_N_TICKS',
            4,
        ),
        'max_consecutive_failures': _env_int(
            'MARJON_U001_AUTOMATION_MAX_CONSECUTIVE_FAILURES',
            3,
        ),
        'connectivity_pause_after_errors': _env_int(
            'MARJON_U001_AUTOMATION_CONNECTIVITY_PAUSE_AFTER_ERRORS',
            2,
        ),
        'connectivity_pause_hours': _env_float(
            'MARJON_U001_AUTOMATION_CONNECTIVITY_PAUSE_HOURS',
            1,
        ),
        'failure_pause_hours': _env_int(
            'MARJON_U001_AUTOMATION_FAILURE_PAUSE_HOURS',
            2,
        ),
        'max_guarded_per_day': _env_int(
            'MARJON_U001_AUTOMATION_MAX_GUARDED_PER_DAY',
            1,
        ),
        'rd001_recent_cooldown_hours': _env_float(
            'MARJON_U001_AUTOMATION_RD001_RECENT_COOLDOWN_HOURS',
            1,
        ),
        'rd001_recent_runner_fresh_minutes': _env_int(
            'MARJON_U001_AUTOMATION_RD001_RECENT_RUNNER_FRESH_MINUTES',
            10,
        ),
        'rd001_partial_hist_cooldown_hours': _env_float(
            'MARJON_U001_AUTOMATION_RD001_PARTIAL_HIST_COOLDOWN_HOURS',
            0.4,
        ),
        'truth_rd001_chain_audit_cooldown_hours': _env_float(
            'MARJON_U001_AUTOMATION_TRUTH_RD001_CHAIN_AUDIT_COOLDOWN_HOURS',
            24,
        ),
        'truth_fl001_derived_audit_cooldown_hours': _env_float(
            'MARJON_U001_AUTOMATION_TRUTH_FL001_DERIVED_AUDIT_COOLDOWN_HOURS',
            24,
        ),
        'rd001_error_cooldown_hours': _env_float(
            'MARJON_U001_AUTOMATION_RD001_ERROR_COOLDOWN_HOURS',
            2,
        ),
        'rd001_transport_pause_hours': _env_float(
            'MARJON_U001_AUTOMATION_RD001_TRANSPORT_PAUSE_HOURS',
            2,
        ),
        'rd001_guarded_cooldown_hours': _env_float(
            'MARJON_U001_AUTOMATION_RD001_GUARDED_COOLDOWN_HOURS',
            12,
        ),
        'rd001_guarded_max_filtered_signatures': _env_int(
            'MARJON_U001_RD001_GUARDED_MAX_FILTERED_SIGNATURES',
            1400,
        ),
        'snapshot_hour_utc': _env_int(
            'MARJON_U001_AUTOMATION_SNAPSHOT_HOUR_UTC',
            0,
        ),
        'daily_days': _env_int('MARJON_U001_DAILY_DAYS', 20),
        'daily_coins': _env_int('MARJON_U001_DAILY_COINS', 50),
        'daily_mature_only': os.environ.get('MARJON_U001_DAILY_MATURE_ONLY', '1') == '1',
        'pool_mapping_recent_days': _env_int('MARJON_U001_POOL_MAPPING_RECENT_DAYS', SHYFT_RETENTION_DAYS),
        'pool_mapping_recent_coins': _env_int('MARJON_U001_POOL_MAPPING_RECENT_COINS', RECENT_COHORT_SIZE),
        'holders_days': _env_int('MARJON_U001_HOLDERS_DAYS', 20),
        'holders_coins': _env_int('MARJON_U001_HOLDERS_COINS', 10),
        'holders_mature_only': os.environ.get('MARJON_U001_HOLDERS_MATURE_ONLY', '1') == '1',
        'holders_force_after_recent_ticks': _env_int(
            'MARJON_U001_AUTOMATION_HOLDERS_FORCE_AFTER_RECENT_TICKS',
            4,
        ),
        'rd001_recent_max_coins': _env_int('MARJON_U001_RD001_MAX_COINS', 25),
        'rd001_partial_hist_max_coins': _env_int('MARJON_U001_RD001_PARTIAL_HIST_MAX_COINS', 5),
        'rd001_error_max_coins': _env_int('MARJON_U001_RD001_ERROR_MAX_COINS', 10),
        'rd001_guarded_max_coins': _env_int('MARJON_U001_RD001_PARTIAL_GUARDED_MAX_COINS', 1),
    }


def collect_metrics(now=None):
    """Collect the minimum current-state metrics needed for action selection."""
    now = now or timezone.now()
    recent_cutoff = now - timedelta(days=SHYFT_RETENTION_DAYS)
    latest_coin_ingested = MigratedCoin.objects.order_by('-ingested_at').values_list(
        'ingested_at', flat=True,
    ).first()

    recent_coin_ids = list(
        MigratedCoin.objects.exclude(anchor_event__isnull=True)
        .order_by('-anchor_event')
        .values_list('mint_address', flat=True)[:RECENT_COHORT_SIZE]
    )
    recent_discovered_count = len(recent_coin_ids)
    recent_mapped_count = (
        PoolMapping.objects.filter(coin_id__in=recent_coin_ids)
        .values('coin_id').distinct().count()
        if recent_coin_ids else 0
    )

    mature_count = MigratedCoin.objects.filter(
        anchor_event__lte=now - MigratedCoin.OBSERVATION_WINDOW_END,
    ).count()
    fl002_complete_count = U001PipelineStatus.objects.filter(
        layer_id='FL-002',
        status='window_complete',
    ).count()

    recent_mapped_ids = set(
        PoolMapping.objects.filter(
            coin__anchor_event__gte=recent_cutoff,
        ).values_list('coin_id', flat=True)
    )
    recent_complete_ids = set(
        U001PipelineStatus.objects.filter(
            layer_id='RD-001',
            status='window_complete',
            coin_id__in=recent_mapped_ids,
        ).values_list('coin_id', flat=True)
    )
    safe_recent_ids = set(
        RawTransaction.objects.filter(
            coin_id__in=recent_mapped_ids,
        ).values_list('coin_id', flat=True).distinct()
    )
    safe_recent_ids.update(
        U001PipelineStatus.objects.filter(
            layer_id='RD-001',
            coin_id__in=recent_mapped_ids,
            watermark__isnull=False,
        ).values_list('coin_id', flat=True)
    )
    recent_pending_ids = recent_mapped_ids - recent_complete_ids
    recent_safe_count = len(recent_pending_ids & safe_recent_ids)
    recent_bootstrap_count = len(recent_pending_ids - safe_recent_ids)

    historical_partial_count = U001PipelineStatus.objects.filter(
        layer_id='RD-001',
        status='partial',
        coin__anchor_event__lt=recent_cutoff,
    ).exclude(
        last_error__icontains=FREE_TIER_GUARD_TEXT,
    ).count()

    rd001_error_count = U001PipelineStatus.objects.filter(
        layer_id='RD-001',
        status='error',
    ).exclude(
        last_error__icontains=FREE_TIER_GUARD_TEXT,
    ).count()

    guarded_count = U001PipelineStatus.objects.filter(
        layer_id='RD-001',
        status__in=('partial', 'error'),
        coin__anchor_event__lt=recent_cutoff,
        last_error__icontains=FREE_TIER_GUARD_TEXT,
    ).count()
    latest_source_audit_at = (
        U001SourceAuditRun.objects.order_by('-completed_at', '-started_at', '-id')
        .values_list('completed_at', 'started_at')
        .first()
    )
    latest_rd001_chain_audit_at = (
        U001RD001ChainAuditRun.objects.order_by('-completed_at', '-started_at', '-id')
        .values_list('completed_at', 'started_at')
        .first()
    )
    latest_fl001_derived_audit_at = (
        U001FL001DerivedAuditRun.objects.order_by('-completed_at', '-started_at', '-id')
        .values_list('completed_at', 'started_at')
        .first()
    )

    return {
        'now': now,
        'recent_cutoff': recent_cutoff,
        'latest_coin_ingested': latest_coin_ingested,
        'recent_discovered_count': recent_discovered_count,
        'recent_mapped_count': recent_mapped_count,
        'mature_count': mature_count,
        'fl002_complete_count': fl002_complete_count,
        'recent_safe_count': recent_safe_count,
        'recent_bootstrap_count': recent_bootstrap_count,
        'historical_partial_count': historical_partial_count,
        'rd001_error_count': rd001_error_count,
        'guarded_count': guarded_count,
        'latest_source_audit_at': (
            latest_source_audit_at[0] or latest_source_audit_at[1]
            if latest_source_audit_at else None
        ),
        'latest_rd001_chain_audit_at': (
            latest_rd001_chain_audit_at[0] or latest_rd001_chain_audit_at[1]
            if latest_rd001_chain_audit_at else None
        ),
        'latest_fl001_derived_audit_at': (
            latest_fl001_derived_audit_at[0] or latest_fl001_derived_audit_at[1]
            if latest_fl001_derived_audit_at else None
        ),
    }


def select_next_action(state, metrics, force_action=None):
    """Choose exactly one automation action from current state."""
    config = policy_config()
    if force_action:
        return _forced_decision(force_action, config)

    now = metrics['now']
    if _failure_pause_active(
        state,
        max_failures=config['max_consecutive_failures'],
        pause_hours=config['failure_pause_hours'],
        now=now,
    ):
        return AutomationDecision(
            action='no_action',
            reason=(
                'Controller is in cooldown after repeated failed automation ticks. '
                'Wait for the failure pause window or force a lane manually.'
            ),
            command=None,
            kwargs={},
        )
    if _connectivity_pause_active(
        pause_after_errors=config['connectivity_pause_after_errors'],
        pause_hours=config['connectivity_pause_hours'],
        now=now,
    ):
        return AutomationDecision(
            action='no_action',
            reason=(
                'Controller is pausing because recent automation failures look like internet or upstream '
                'reachability problems. Wait for connectivity to recover or force a lane manually.'
            ),
            command=None,
            kwargs={},
        )

    stale_cutoff = now - timedelta(hours=config['discovery_stale_hours'])
    latest_coin_ingested = metrics['latest_coin_ingested']
    holders_auth_paused = _action_error_pause_active(
        state,
        action='holders_catchup',
        pause_hours=config['holders_auth_pause_hours'],
        now=now,
        patterns=AUTH_ERROR_PATTERNS,
    )
    holders_no_progress_paused = _holders_no_progress_pause_active(
        now=now,
        pause_after_ticks=config['holders_no_progress_after_ticks'],
        pause_hours=config['holders_no_progress_pause_hours'],
    )
    rd001_recent_paused = _action_error_pause_active(
        state,
        action='rd001_recent',
        pause_hours=config['rd001_transport_pause_hours'],
        now=now,
        patterns=TRANSPORT_ERROR_PATTERNS,
    )
    rd001_recent_owned_by_runner = _dedicated_recent_runner_active(
        now=now,
        fresh_minutes=config['rd001_recent_runner_fresh_minutes'],
    )

    recent_mapping_pct = (
        metrics['recent_mapped_count'] / metrics['recent_discovered_count']
        if metrics['recent_discovered_count'] else 1.0
    )
    fl002_complete_pct = (
        metrics['fl002_complete_count'] / metrics['mature_count']
        if metrics['mature_count'] else 1.0
    )
    holders_due = (
        fl002_complete_pct < config['fl002_min_complete_pct']
        and not holders_auth_paused
        and not holders_no_progress_paused
        and _cooldown_ok(state, 'holders_catchup', config['holders_cooldown_hours'], now)
    )

    if latest_coin_ingested is None or latest_coin_ingested < stale_cutoff:
        return _decision(
            'refresh_core',
            'Discovery freshness is past the configured stale threshold.',
            _refresh_core_kwargs(config),
        )

    if (
        holders_due
        and _recent_success_streak({'pool_mapping_recent', 'rd001_recent'})
        >= config['holders_force_after_recent_ticks']
    ):
        return _decision(
            'holders_catchup',
            'FL-002 coverage is below the configured floor and recent-focused automation has run for several consecutive ticks.',
            _holders_kwargs(config),
        )

    if (
        metrics['recent_discovered_count']
        and recent_mapping_pct < config['recent_mapping_min_pct']
        and _cooldown_ok(
            state,
            'pool_mapping_recent',
            config['pool_mapping_recent_cooldown_hours'],
            now,
        )
    ):
        return _decision(
            'pool_mapping_recent',
            'Recent discovery exists, but recent pool mapping still needs dedicated catch-up throughput.',
            _pool_mapping_recent_kwargs(config),
        )

    if (
        (metrics['recent_safe_count'] > 0 or metrics['recent_bootstrap_count'] > 0)
        and not rd001_recent_owned_by_runner
        and not rd001_recent_paused
        and _cooldown_ok(state, 'rd001_recent', config['rd001_recent_cooldown_hours'], now)
    ):
        return _decision(
            'rd001_recent',
            (
                'Safe recent RD-001 candidates are available inside Shyft retention.'
                if metrics['recent_safe_count'] > 0 else
                'Recent mapped RD-001 bootstrap candidates are available inside Shyft retention.'
            ),
            {
                'max_coins': config['rd001_recent_max_coins'],
            },
        )

    # When the dedicated recent runner is healthy, the main controller should spend
    # its freed capacity on historical RD-001 catch-up before lower-urgency holders
    # and truth-audit lanes.
    if rd001_recent_owned_by_runner:
        error_due = state.error_lane_tick_counter >= max(config['error_every_n_ticks'] - 1, 0)
        if metrics['rd001_error_count'] > 0 and error_due and _cooldown_ok(
            state,
            'rd001_error_recovery',
            config['rd001_error_cooldown_hours'],
            now,
        ):
            return _decision(
                'rd001_error_recovery',
                'The dedicated recent RD-001 runner is healthy, so the main controller is spending this tick on scheduled RD-001 error recovery.',
                {
                    'source': 'helius',
                    'status_filter': 'error',
                    'max_coins': config['rd001_error_max_coins'],
                },
            )

        if (
            metrics['historical_partial_count'] > 0
            and _cooldown_ok(
                state,
                'rd001_partial_historical',
                config['rd001_partial_hist_cooldown_hours'],
                now,
            )
        ):
            return _decision(
                'rd001_partial_historical',
                'The dedicated recent RD-001 runner is healthy, so the main controller is prioritizing historical RD-001 partial catch-up.',
                {
                    'source': 'helius',
                    'status_filter': 'partial',
                    'max_coins': config['rd001_partial_hist_max_coins'],
                },
            )

        if (
            metrics['guarded_count'] > 0
            and metrics['historical_partial_count'] == 0
            and metrics['rd001_error_count'] == 0
            and state.guarded_attempts_today < config['max_guarded_per_day']
            and _cooldown_ok(
                state,
                'rd001_guarded',
                config['rd001_guarded_cooldown_hours'],
                now,
            )
        ):
            return _decision(
                'rd001_guarded',
                'The dedicated recent RD-001 runner is healthy, so the main controller is using guarded budget on historical RD-001 backlog.',
                {
                    'source': 'helius',
                    'status_filter': 'partial',
                    'include_free_tier_guarded': True,
                    'only_free_tier_guarded': True,
                    'max_coins': config['rd001_guarded_max_coins'],
                    'max_filtered_signatures': config['rd001_guarded_max_filtered_signatures'],
                },
            )

    if _truth_audit_due(
        metrics['latest_rd001_chain_audit_at'],
        config['truth_rd001_chain_audit_cooldown_hours'],
        now,
    ):
        return _decision(
            'truth_rd001_chain_audit',
            'Recent direct-RPC RD-001 chain-truth coverage is stale or missing.',
            {},
        )

    if _truth_audit_due(
        metrics['latest_fl001_derived_audit_at'],
        config['truth_fl001_derived_audit_cooldown_hours'],
        now,
    ):
        return _decision(
            'truth_fl001_derived_audit',
            'Recent self-derived FL-001 truth coverage is stale or missing.',
            {},
        )

    if holders_due:
        return _decision(
            'holders_catchup',
            'FL-002 mature coverage remains below the configured floor.',
            _holders_kwargs(config),
        )

    if _truth_audit_due(
        metrics['latest_source_audit_at'],
        config['truth_source_audit_cooldown_hours'],
        now,
    ):
        return _decision(
            'truth_source_audit',
            'Recent provider-source truth coverage is stale or missing.',
            {},
        )

    if (
        metrics['historical_partial_count'] > 0
        and _cooldown_ok(
            state,
            'rd001_partial_historical',
            config['rd001_partial_hist_cooldown_hours'],
            now,
        )
    ):
        return _decision(
            'rd001_partial_historical',
            'Historical RD-001 partial backlog is available and recent safe work is thin.',
            {
                'source': 'helius',
                'status_filter': 'partial',
                'max_coins': config['rd001_partial_hist_max_coins'],
            },
        )

    error_due = state.error_lane_tick_counter >= max(config['error_every_n_ticks'] - 1, 0)
    if metrics['rd001_error_count'] > 0 and (
        error_due or (
            metrics['recent_safe_count'] == 0
            and metrics['historical_partial_count'] == 0
        )
    ) and _cooldown_ok(
        state,
        'rd001_error_recovery',
        config['rd001_error_cooldown_hours'],
        now,
    ):
        return _decision(
            'rd001_error_recovery',
            'RD-001 error rows are due for a controlled retry pass.',
            {
                'source': 'helius',
                'status_filter': 'error',
                'max_coins': config['rd001_error_max_coins'],
            },
        )

    if (
        metrics['guarded_count'] > 0
        and state.guarded_attempts_today < config['max_guarded_per_day']
        and _cooldown_ok(
            state,
            'rd001_guarded',
            config['rd001_guarded_cooldown_hours'],
            now,
        )
    ):
        return _decision(
            'rd001_guarded',
            'Free-tier-guarded historical RD-001 rows remain and today still has guarded budget left.',
            {
                'source': 'helius',
                'status_filter': 'partial',
                'include_free_tier_guarded': True,
                'only_free_tier_guarded': True,
                'max_coins': config['rd001_guarded_max_coins'],
                'max_filtered_signatures': config['rd001_guarded_max_filtered_signatures'],
            },
        )

    return AutomationDecision(
        action='no_action',
        reason='No enabled lane currently beats the controller thresholds.',
        command=None,
        kwargs={},
    )


def snapshot_due(state, now=None):
    """Return True when today has no snapshot yet and the scheduled hour has passed."""
    now = now or timezone.now()
    config = policy_config()
    return (
        state.last_snapshot_date != timezone.localdate(now)
        and now.hour >= config['snapshot_hour_utc']
    )


def _refresh_core_kwargs(config):
    kwargs = {
        'universe': 'u001',
        'steps': 'discovery,pool_mapping,ohlcv',
        'days': config['daily_days'],
        'coins': config['daily_coins'],
    }
    if config['daily_mature_only']:
        kwargs['mature_only'] = True
    return kwargs


def _holders_kwargs(config):
    kwargs = {
        'universe': 'u001',
        'steps': 'holders',
        'days': config['holders_days'],
        'coins': config['holders_coins'],
    }
    if config['holders_mature_only']:
        kwargs['mature_only'] = True
    return kwargs


def _pool_mapping_recent_kwargs(config):
    return {
        'universe': 'u001',
        'steps': 'pool_mapping',
        'days': config['pool_mapping_recent_days'],
        'coins': max(config['pool_mapping_recent_coins'], RECENT_COHORT_SIZE),
    }


def _decision(action, reason, kwargs):
    command_map = {
        'refresh_core': 'orchestrate',
        'truth_source_audit': 'audit_u001_sources',
        'pool_mapping_recent': 'orchestrate',
        'holders_catchup': 'orchestrate',
        'rd001_recent': 'fetch_transactions_batch',
        'truth_rd001_chain_audit': 'audit_u001_rd001_chain',
        'truth_fl001_derived_audit': 'audit_u001_fl001_derived',
        'rd001_partial_historical': 'fetch_transactions_batch',
        'rd001_error_recovery': 'fetch_transactions_batch',
        'rd001_guarded': 'fetch_transactions_batch',
    }
    command = command_map.get(action)
    return AutomationDecision(
        action=action,
        reason=reason,
        command=command,
        kwargs=kwargs,
    )


def _forced_decision(action, config):
    if action == 'refresh_core':
        return _decision(action, 'Forced action override.', _refresh_core_kwargs(config))
    if action == 'truth_source_audit':
        return _decision(action, 'Forced action override.', {})
    if action == 'pool_mapping_recent':
        return _decision(action, 'Forced action override.', _pool_mapping_recent_kwargs(config))
    if action == 'holders_catchup':
        return _decision(action, 'Forced action override.', _holders_kwargs(config))
    if action == 'rd001_recent':
        return _decision(action, 'Forced action override.', {'max_coins': config['rd001_recent_max_coins']})
    if action == 'truth_rd001_chain_audit':
        return _decision(action, 'Forced action override.', {})
    if action == 'truth_fl001_derived_audit':
        return _decision(action, 'Forced action override.', {})
    if action == 'rd001_partial_historical':
        return _decision(
            action,
            'Forced action override.',
            {'source': 'helius', 'status_filter': 'partial', 'max_coins': config['rd001_partial_hist_max_coins']},
        )
    if action == 'rd001_error_recovery':
        return _decision(
            action,
            'Forced action override.',
            {'source': 'helius', 'status_filter': 'error', 'max_coins': config['rd001_error_max_coins']},
        )
    if action == 'rd001_guarded':
        return _decision(
            action,
            'Forced action override.',
            {
                'source': 'helius',
                'status_filter': 'partial',
                'include_free_tier_guarded': True,
                'only_free_tier_guarded': True,
                'max_coins': config['rd001_guarded_max_coins'],
                'max_filtered_signatures': config['rd001_guarded_max_filtered_signatures'],
            },
        )
    return AutomationDecision(
        action='no_action',
        reason='Forced action override.',
        command=None,
        kwargs={},
    )


def _cooldown_ok(state, action, hours, now):
    if state.last_action != action or not state.last_action_completed_at:
        return True
    return state.last_action_completed_at <= now - timedelta(hours=hours)


def _failure_pause_active(state, max_failures, pause_hours, now):
    if state.consecutive_failures < max_failures:
        return False
    if state.last_action_status != 'error' or not state.last_action_completed_at:
        return False
    return state.last_action_completed_at > now - timedelta(hours=pause_hours)


def _connectivity_pause_active(pause_after_errors, pause_hours, now, limit=5):
    if pause_after_errors <= 0:
        return False
    ticks = list(
        U001AutomationTick.objects.exclude(action='no_action')
        .order_by('-started_at', '-id')[:limit]
    )
    streak = []
    for tick in ticks:
        note = (tick.notes or '').lower()
        if (
            tick.status != 'error'
            or not tick.completed_at
            or not any(pattern in note for pattern in TRANSPORT_ERROR_PATTERNS)
        ):
            break
        streak.append(tick)
    if len(streak) < pause_after_errors:
        return False
    return streak[0].completed_at > now - timedelta(hours=pause_hours)


def _action_error_pause_active(state, action, pause_hours, now, patterns):
    if state.last_action != action:
        return False
    if state.last_action_status != 'error' or not state.last_action_completed_at:
        return False
    note = (state.notes or '').lower()
    if not any(pattern in note for pattern in patterns):
        return False
    return state.last_action_completed_at > now - timedelta(hours=pause_hours)


def _truth_audit_due(latest_run_at, cooldown_hours, now):
    if latest_run_at is None:
        return True
    return latest_run_at <= now - timedelta(hours=cooldown_hours)


def _dedicated_recent_runner_active(now, fresh_minutes):
    if fresh_minutes <= 0:
        return False
    status = read_status_file()
    if not status or status.get('_error'):
        return False
    if status.get('state') not in {'running', 'sleeping'}:
        return False
    if not pid_alive(status.get('pid')):
        return False
    updated_at = parse_runner_datetime(status.get('updated_at'))
    if not updated_at:
        return False
    return updated_at >= now - timedelta(minutes=fresh_minutes)


def _holders_no_progress_pause_active(now, pause_after_ticks, pause_hours):
    if pause_after_ticks <= 0:
        return False
    ticks = list(
        U001AutomationTick.objects.filter(action='holders_catchup')
        .order_by('-started_at', '-id')[:pause_after_ticks]
    )
    if len(ticks) < pause_after_ticks:
        return False
    for tick in ticks:
        if tick.status != 'complete' or not tick.completed_at:
            return False
        loaded = (
            ((tick.result_summary or {}).get('steps') or {})
            .get('holders', {})
            .get('records_loaded')
        )
        if loaded != 0:
            return False
    return ticks[0].completed_at > now - timedelta(hours=pause_hours)


def _recent_success_streak(actions):
    streak = 0
    for tick in U001AutomationTick.objects.only('action', 'status').order_by('-started_at', '-id')[:20]:
        if tick.status != 'complete' or tick.action not in actions:
            break
        streak += 1
    return streak
