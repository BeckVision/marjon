"""U-001 operations overview helpers for the diagnostics cockpit."""

from collections import Counter
from datetime import datetime, timedelta, timezone as dt_timezone
import re

from django.conf import settings
from django.db.models import Max, Min
from django.utils import timezone

from pipeline.management.commands.fetch_transactions import SHYFT_RETENTION_DAYS
from pipeline.u001_rd001_recent_runner import (
    last_successful_cycle_at,
    parse_runner_datetime,
    pid_alive,
    read_status_file,
    status_path,
)
from pipeline.u001_automation import collect_metrics, select_next_action
from warehouse.models import (
    U001FL001DerivedAuditRun,
    HolderSnapshot,
    MigratedCoin,
    OHLCVCandle,
    PipelineBatchRun,
    PoolMapping,
    RawTransaction,
    SkippedTransaction,
    U001AutomationState,
    U001AutomationTick,
    U001BootRecoveryRun,
    U001OpsSnapshot,
    U001PipelineRun,
    U001PipelineStatus,
    U001RD001ChainAuditRun,
    U001SourceAuditRun,
)

FREE_TIER_GUARD_TEXT = 'exceeds free-tier guard'
STATUS_ORDER = (
    'window_complete',
    'partial',
    'in_progress',
    'error',
    'not_started',
)
ERROR_BUCKET_ORDER = (
    'auth',
    'transport',
    'free_tier_guard',
    'expectation_failed',
    'rate_limited',
    'server_error',
    'response_validation',
    'json_decode',
    'other',
)
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
LAYER_MODELS = {
    'FL-001': OHLCVCandle,
    'FL-002': HolderSnapshot,
    'RD-001': RawTransaction,
}
LAYER_TITLES = {
    'FL-001': 'FL-001',
    'FL-002': 'FL-002',
    'RD-001': 'RD-001',
}
COVERAGE_PRESETS = {
    '100': ('count', 100, 'Last 100 discovered coins'),
    '500': ('count', 500, 'Last 500 discovered coins'),
    '1000': ('count', 1000, 'Last 1000 discovered coins'),
    '14d': ('days', 14, 'Last 14 days'),
    '30d': ('days', 30, 'Last 30 days'),
}
AUTOMATION_SPIN_STREAK_THRESHOLD = 4


def classify_error_bucket(error):
    """Group a last_error string into the same operator buckets as health output."""
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


def build_overview_summary(stale_days=3, recent_runs=5):
    """Return a server-renderable U-001 diagnostics summary."""
    now = timezone.now()
    stale_window = timedelta(days=stale_days)

    total_coins = MigratedCoin.objects.count()
    mature_count = MigratedCoin.objects.filter(
        anchor_event__lte=now - MigratedCoin.OBSERVATION_WINDOW_END,
    ).count()
    mapped_count = PoolMapping.objects.values('coin_id').distinct().count()

    latest_coin_anchor = MigratedCoin.objects.aggregate(v=Max('anchor_event'))['v']
    latest_coin_ingested = MigratedCoin.objects.aggregate(v=Max('ingested_at'))['v']

    freshness = [
        _freshness_item(
            'Discovery Anchor',
            latest_coin_anchor,
            now,
            warn_after=stale_window,
            critical_after=stale_window * 2,
        ),
        _freshness_item(
            'Discovery Ingested',
            latest_coin_ingested,
            now,
            warn_after=stale_window,
            critical_after=stale_window * 2,
        ),
    ]
    for layer_id, model in LAYER_MODELS.items():
        freshness.append(
            _freshness_item(
                f'{layer_id} Ingested',
                model.objects.aggregate(v=Max('ingested_at'))['v'],
                now,
                warn_after=stale_window,
                critical_after=stale_window * 2,
            )
        )

    cards = [
        _discovery_card(total_coins, mature_count, latest_coin_ingested),
        _pool_mapping_card(total_coins, mapped_count),
    ]
    for layer_id, model in LAYER_MODELS.items():
        cards.append(
            _layer_card(
                layer_id=layer_id,
                model=model,
                eligible_count=_eligible_count(layer_id, total_coins, mapped_count),
            )
        )

    error_panels = []
    for layer_id in ('FL-002', 'RD-001'):
        buckets = _error_bucket_counts(layer_id)
        if buckets:
            error_panels.append({
                'layer_id': layer_id,
                'title': LAYER_TITLES[layer_id],
                'buckets': buckets,
                'dominant_bucket': max(buckets, key=buckets.get),
                'dominant_count': max(buckets.values()),
            })

    recent_batches = [_serialize_batch_run(batch, now) for batch in PipelineBatchRun.objects.filter(
        pipeline_id='U-001',
    ).order_by('-started_at')[:recent_runs]]

    recent_coverage = _recent_coverage_snapshot()
    recommendation = _recommended_action(
        now=now,
        latest_coin_ingested=latest_coin_ingested,
        stale_cutoff=now - stale_window,
        total_coins=total_coins,
        mapped_count=mapped_count,
        rd001_card=cards[-1],
        rd001_error_buckets=_error_bucket_counts('RD-001'),
        recent_coverage=recent_coverage,
    )

    return {
        'generated_at': now,
        'headline': {
            'total_coins': total_coins,
            'mature_coins': mature_count,
            'mapped_coins': mapped_count,
        },
        'automation': _automation_summary(),
        'rd001_recent_runner': _rd001_recent_runner_summary(now),
        'boot_recovery': _boot_recovery_summary(now),
        'connectivity_risk': _connectivity_risk_summary(now=now),
        'spin_risk': _automation_spin_summary(now=now),
        'truth_audit_coverage': _recent_truth_audit_coverage_summary(now=now, days=7),
        'source_audit': _source_audit_summary(now),
        'rd001_chain_audit': _rd001_chain_audit_summary(now),
        'fl001_derived_audit': _fl001_derived_audit_summary(now),
        'freshness': freshness,
        'cards': cards,
        'error_panels': error_panels,
        'recent_batches': recent_batches,
        'recent_coverage': recent_coverage,
        'recommendation': recommendation,
    }


def build_coverage_summary(preset='1000'):
    """Return a funnel summary for a recent discovery slice."""
    now = timezone.now()
    scope = _coverage_scope(preset, now)
    coin_ids = scope['coin_ids']
    discovered_count = len(coin_ids)

    stages = []
    if discovered_count:
        stage_defs = [
            ('discovered', 'Discovered', discovered_count),
            ('mapped', 'Pool mapped', _distinct_count(PoolMapping, coin_ids)),
            ('fl001_status', 'FL-001 has status', _status_stage_count('FL-001', coin_ids)),
            ('fl001_complete', 'FL-001 window_complete', _status_stage_count('FL-001', coin_ids, 'window_complete')),
            ('fl002_status', 'FL-002 has status', _status_stage_count('FL-002', coin_ids)),
            ('fl002_complete', 'FL-002 window_complete', _status_stage_count('FL-002', coin_ids, 'window_complete')),
            ('rd001_status', 'RD-001 has status', _status_stage_count('RD-001', coin_ids)),
            ('rd001_complete', 'RD-001 window_complete', _status_stage_count('RD-001', coin_ids, 'window_complete')),
        ]

        previous = None
        for key, label, count in stage_defs:
            delta = 0 if previous is None else previous - count
            stages.append({
                'key': key,
                'label': label,
                'count': count,
                'pct_of_discovered': _completion_pct(count, discovered_count),
                'delta_from_prior': delta,
            })
            previous = count

    bottleneck = _coverage_bottleneck(stages, coin_ids)
    return {
        'preset': scope['preset'],
        'preset_label': scope['label'],
        'available_presets': _coverage_preset_list(scope['preset']),
        'generated_at': now,
        'discovered_count': discovered_count,
        'stages': stages,
        'range_start': scope['range_start'],
        'range_end': scope['range_end'],
        'bottleneck': bottleneck,
    }


def build_queue_summary(limit=8):
    """Return a read-only summary of the main RD-001 operating lanes."""
    now = timezone.now()
    recent_cutoff = now - timedelta(days=SHYFT_RETENTION_DAYS)
    status_rows = _rd001_status_rows()
    mapped_coins = list(
        MigratedCoin.objects.filter(
            mint_address__in=PoolMapping.objects.values_list('coin_id', flat=True),
        ).order_by('-anchor_event')
    )
    raw_tx_mints = set(
        RawTransaction.objects.values_list('coin_id', flat=True).distinct()
    )

    recent_safe = []
    recent_risky = []
    historical_partial = []
    historical_guarded = []
    error_lane = []

    for coin in mapped_coins:
        status = status_rows.get(coin.mint_address)
        status_value = status['status'] if status else 'not_started'
        if status_value == 'window_complete':
            continue

        has_watermark = (
            coin.mint_address in raw_tx_mints
            or bool(status and status.get('watermark'))
        )
        is_recent = bool(coin.anchor_event and coin.anchor_event >= recent_cutoff)
        age_label = _age_label(coin.anchor_event, now) if coin.anchor_event else 'Unknown'
        bucket = classify_error_bucket(status['last_error']) if status else None
        queue_item = {
            'mint': coin.mint_address,
            'symbol': coin.symbol or 'Unknown',
            'anchor_event': coin.anchor_event,
            'age_label': age_label,
            'status': status_value,
            'last_run_at': status['last_run_at'] if status else None,
            'last_error': status['last_error'] if status else None,
            'last_error_bucket': bucket,
            'has_watermark': has_watermark,
        }

        if is_recent:
            if has_watermark:
                recent_safe.append(queue_item)
            else:
                risky_item = dict(queue_item)
                risky_item['skip_reason'] = (
                    'No RD-001 watermark yet. The batch selector applies stricter '
                    'bootstrap caps before spending a full-window run.'
                )
                recent_risky.append(risky_item)
            continue

        if status_value == 'partial':
            if _is_free_tier_guarded(status['last_error'] if status else None):
                historical_guarded.append(_guarded_item(queue_item))
            else:
                historical_partial.append(queue_item)

        if status_value == 'error':
            if _is_free_tier_guarded(status['last_error'] if status else None):
                historical_guarded.append(_guarded_item(queue_item))
            else:
                error_lane.append(queue_item)

    min_dt = _min_aware_datetime()

    recent_safe = sorted(
        recent_safe,
        key=lambda item: (
            item['last_run_at'] or min_dt,
            item['mint'],
        ),
    )[:limit]
    recent_risky = sorted(
        recent_risky,
        key=lambda item: (item['anchor_event'] or min_dt, item['mint']),
        reverse=True,
    )[:limit]
    historical_partial = sorted(
        historical_partial,
        key=lambda item: (
            item['last_run_at'] or min_dt,
            item['mint'],
        ),
    )[:limit]
    historical_guarded = sorted(
        historical_guarded,
        key=lambda item: (
            item['guard_count'] if item['guard_count'] is not None else float('inf'),
            item['last_run_at'] or min_dt,
            item['mint'],
        ),
    )[:limit]
    error_lane = sorted(
        error_lane,
        key=lambda item: (
            item['last_run_at'] or min_dt,
            item['mint'],
        ),
    )[:limit]

    return {
        'generated_at': now,
        'recent_cutoff': recent_cutoff,
        'queues': [
            {
                'key': 'recent_safe',
                'title': 'Recent safe Shyft steady-state lane',
                'description': 'Recent mapped coins that already have an RD-001 watermark and are the safest recent candidates.',
                'columns': ['mint', 'anchor_event', 'age_label', 'has_watermark', 'status'],
                'items': recent_safe,
                'command': './scripts/run_batch.sh --max-coins 3 --max-new-sigs 1000',
                'empty_reason': 'No recent mapped coins currently have a reusable RD-001 watermark.',
            },
            {
                'key': 'recent_risky',
                'title': 'Recent risky bootstrap lane',
                'description': 'Recent mapped coins with no RD-001 watermark yet, so they need the stricter bootstrap safety caps.',
                'columns': ['mint', 'anchor_event', 'status', 'skip_reason'],
                'items': recent_risky,
                'command': './scripts/run_batch.sh --dry-run --max-coins 5 --max-new-sigs 1000',
                'empty_reason': 'There are no unmapped bootstrap-style recent RD-001 candidates in this snapshot.',
            },
            {
                'key': 'historical_partial',
                'title': 'Historical Helius partial lane',
                'description': 'Old RD-001 partial rows outside Shyft retention that are not currently parked by the free-tier guard.',
                'columns': ['mint', 'status', 'last_run_at', 'age_label'],
                'items': historical_partial,
                'command': './scripts/run_batch_partials_historical.sh --max-coins 3',
                'empty_reason': 'There are no historical non-guarded RD-001 partial rows right now.',
            },
            {
                'key': 'historical_guarded',
                'title': 'Historical guarded Helius lane',
                'description': 'Old RD-001 rows whose last failure tripped the free-tier guard and need deliberate one-off handling.',
                'columns': ['mint', 'guard_count', 'status', 'last_error'],
                'items': historical_guarded,
                'command': 'MARJON_U001_RD001_MAX_FILTERED_SIGNATURES=1400 ./scripts/run_batch_partials_guarded.sh',
                'empty_reason': 'There are no currently parked free-tier-guarded historical RD-001 rows.',
            },
            {
                'key': 'error_lane',
                'title': 'Error lane',
                'description': 'RD-001 rows currently in error state, excluding the explicitly guarded historical queue above.',
                'columns': ['mint', 'status', 'last_error_bucket', 'last_run_at'],
                'items': error_lane,
                'command': './scripts/run_batch_errors.sh --max-new-sigs 1000',
                'empty_reason': 'There are no current non-guarded RD-001 error rows.',
            },
        ],
    }


def build_coin_detail_summary(mint_address, run_limit=12):
    """Return a debug-oriented detail payload for one U-001 coin."""
    now = timezone.now()
    coin = MigratedCoin.objects.get(mint_address=mint_address)
    pool_mappings = list(
        PoolMapping.objects.filter(coin_id=coin.mint_address).order_by('discovered_at')
    )
    status_rows = {
        row['layer_id']: row
        for row in U001PipelineStatus.objects.filter(coin_id=coin.mint_address).values(
            'layer_id',
            'status',
            'watermark',
            'last_run_at',
            'last_error',
        )
    }
    runs = list(
        U001PipelineRun.objects.filter(coin_id=coin.mint_address)
        .select_related('batch')
        .order_by('-started_at')[:run_limit]
    )

    ohlcv_counts = OHLCVCandle.objects.filter(coin_id=coin.mint_address)
    holder_counts = HolderSnapshot.objects.filter(coin_id=coin.mint_address)
    raw_counts = RawTransaction.objects.filter(coin_id=coin.mint_address)
    skipped_counts = SkippedTransaction.objects.filter(coin_id=coin.mint_address)

    return {
        'generated_at': now,
        'coin': {
            'mint': coin.mint_address,
            'symbol': coin.symbol or 'Unknown',
            'name': coin.name or 'Unknown',
            'anchor_event': coin.anchor_event,
            'ingested_at': coin.ingested_at,
            'age_label': _age_label(coin.anchor_event, now) if coin.anchor_event else 'Unknown',
            'is_mature': coin.is_mature,
            'window_end_time': coin.window_end_time,
            'pool_mappings': [
                {
                    'pool_address': mapping.pool_address,
                    'dex': mapping.dex,
                    'source': mapping.source,
                    'created_at': mapping.created_at,
                    'discovered_at': mapping.discovered_at,
                }
                for mapping in pool_mappings
            ],
        },
        'layer_statuses': [
            {
                'layer_id': 'DISCOVERY',
                'title': 'Discovery',
                'status': 'window_complete',
                'watermark': coin.anchor_event,
                'last_run_at': coin.ingested_at,
                'last_error': None,
            },
            _coin_layer_status('FL-001', status_rows),
            _coin_layer_status('FL-002', status_rows),
            _coin_layer_status('RD-001', status_rows),
        ],
        'warehouse_counts': {
            'ohlcv_count': ohlcv_counts.count(),
            'holder_count': holder_counts.count(),
            'raw_transaction_count': raw_counts.count(),
            'skipped_transaction_count': skipped_counts.count(),
        },
        'run_history': [
            {
                'layer_id': run.layer_id,
                'mode': run.mode,
                'status': run.status,
                'started_at': run.started_at,
                'completed_at': run.completed_at,
                'records_loaded': run.records_loaded,
                'records_expected': run.records_expected,
                'api_calls': run.api_calls,
                'error_message': run.error_message,
            }
            for run in runs
        ],
        'timeline': _coin_timeline(coin),
    }


def build_trends_summary(days=14):
    """Return a run-history-derived trends summary for the cockpit."""
    now = timezone.now()
    days = max(int(days or 14), 1)
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )

    layer_statuses = {
        layer_id: _status_counts(layer_id)
        for layer_id in ('FL-001', 'FL-002', 'RD-001')
    }
    current_buckets = {
        'rd001': _error_bucket_counts('RD-001'),
        'fl002': _error_bucket_counts('FL-002'),
    }
    snapshots = list(
        U001OpsSnapshot.objects.filter(
            snapshot_date__gte=start.date(),
            snapshot_date__lte=now.date(),
        ).order_by('snapshot_date')
    )

    batch_runs = list(
        PipelineBatchRun.objects.filter(
            pipeline_id='U-001',
            started_at__gte=start,
        ).order_by('started_at')
    )
    pipeline_runs = list(
        U001PipelineRun.objects.filter(
            started_at__gte=start,
        ).order_by('started_at')
    )
    source_audit_runs = list(
        U001SourceAuditRun.objects.filter(
            started_at__gte=start,
        ).order_by('started_at')
    )
    rd001_chain_audit_runs = list(
        U001RD001ChainAuditRun.objects.filter(
            started_at__gte=start,
        ).order_by('started_at')
    )
    fl001_derived_audit_runs = list(
        U001FL001DerivedAuditRun.objects.filter(
            started_at__gte=start,
        ).order_by('started_at')
    )

    daily_rows = _daily_trend_rows(start, days)
    row_by_date = {row['date']: row for row in daily_rows}
    for snapshot in snapshots:
        row = row_by_date.get(snapshot.snapshot_date)
        if not row:
            continue
        row.update({
            'has_snapshot': True,
            'discovered_count': snapshot.discovered_count,
            'mapped_count': snapshot.mapped_count,
            'fl001_complete_count': snapshot.fl001_complete_count,
            'fl002_complete_count': snapshot.fl002_complete_count,
            'rd001_complete_count': snapshot.rd001_complete_count,
            'rd001_partial_count': snapshot.rd001_partial_count,
            'rd001_error_count': snapshot.rd001_error_count,
            'rd001_transport_error_count': snapshot.rd001_transport_error_count,
            'rd001_guard_error_count': snapshot.rd001_guard_error_count,
            'fl002_auth_error_count': snapshot.fl002_auth_error_count,
        })

    for batch in batch_runs:
        row = row_by_date.get(batch.started_at.date())
        if not row:
            continue
        row['batch_started'] += 1
        if batch.status == 'complete':
            row['batch_complete'] += 1
        elif batch.status == 'error':
            row['batch_error'] += 1
        row['coins_succeeded'] += batch.coins_succeeded
        row['coins_failed'] += batch.coins_failed
        row['batch_api_calls'] += batch.api_calls

    for run in pipeline_runs:
        row = row_by_date.get(run.started_at.date())
        if not row:
            continue
        prefix = _trend_prefix(run.layer_id)
        if not prefix:
            continue
        if run.status == 'complete':
            row[f'{prefix}_complete_runs'] += 1
        elif run.status == 'error':
            row[f'{prefix}_error_runs'] += 1
        row[f'{prefix}_records_loaded'] += run.records_loaded
        row[f'{prefix}_api_calls'] += run.api_calls

        bucket = classify_error_bucket(run.error_message)
        if prefix == 'rd001':
            if bucket == 'transport':
                row['rd001_transport_errors'] += 1
            if bucket == 'free_tier_guard':
                row['rd001_guard_errors'] += 1
        if prefix == 'fl002' and bucket == 'auth':
            row['fl002_auth_errors'] += 1

    for run in source_audit_runs:
        _apply_truth_audit_run(
            row_by_date=row_by_date,
            run=run,
            prefix='source_audit',
        )
    for run in rd001_chain_audit_runs:
        _apply_truth_audit_run(
            row_by_date=row_by_date,
            run=run,
            prefix='rd001_chain_audit',
            sample_key='transaction_count',
        )
    for run in fl001_derived_audit_runs:
        _apply_truth_audit_run(
            row_by_date=row_by_date,
            run=run,
            prefix='fl001_derived_audit',
            sample_key='candle_count',
        )

    max_loaded = max((row['rd001_records_loaded'] for row in daily_rows), default=0)
    for row in daily_rows:
        row['rd001_loaded_pct'] = _completion_pct(row['rd001_records_loaded'], max_loaded) if max_loaded else 0.0

    return {
        'generated_at': now,
        'days': days,
        'start': start,
        'note': (
            'Daily backlog rows come from U001OpsSnapshot when available. '
            'Recent batch and layer activity still come from run history. '
            'Dates without a snapshot remain operational approximations only. '
            'Truth-audit rows below come from persisted sampled source, direct-RPC, and derived-candle audits.'
        ),
        'snapshot_days': sum(1 for row in daily_rows if row['has_snapshot']),
        'truth_audits': [
            _source_audit_summary(now),
            _rd001_chain_audit_summary(now),
            _fl001_derived_audit_summary(now),
        ],
        'truth_audit_summary': _truth_audit_trend_summary(daily_rows, days),
        'current_layers': [
            {
                'layer_id': 'FL-001',
                'title': 'FL-001',
                'counts': layer_statuses['FL-001'],
            },
            {
                'layer_id': 'FL-002',
                'title': 'FL-002',
                'counts': layer_statuses['FL-002'],
            },
            {
                'layer_id': 'RD-001',
                'title': 'RD-001',
                'counts': layer_statuses['RD-001'],
            },
        ],
        'current_highlights': [
            {
                'label': 'RD-001 transport statuses',
                'value': current_buckets['rd001'].get('transport', 0),
            },
            {
                'label': 'RD-001 free-tier-guard statuses',
                'value': current_buckets['rd001'].get('free_tier_guard', 0),
            },
            {
                'label': 'FL-002 auth statuses',
                'value': current_buckets['fl002'].get('auth', 0),
            },
            {
                'label': 'RD-001 partial statuses',
                'value': layer_statuses['RD-001'].get('partial', 0),
            },
        ],
        'daily_rows': daily_rows,
    }


def _apply_truth_audit_run(row_by_date, run, prefix, sample_key=None):
    row = row_by_date.get(run.started_at.date())
    if not row:
        return
    row[f'{prefix}_runs'] += 1
    row[f'{prefix}_findings'] += run.finding_count
    row[f'{prefix}_warnings'] += run.warning_count
    if sample_key:
        sample_value = getattr(run, sample_key, 0)
        row[f'{prefix}_sampled'] += sample_value if isinstance(sample_value, int) else 0
    if run.status == 'ok':
        row[f'{prefix}_ok'] += 1
    elif run.status == 'warning':
        row[f'{prefix}_warning_runs'] += 1
    elif run.status == 'finding':
        row[f'{prefix}_finding_runs'] += 1
    elif run.status == 'error':
        row[f'{prefix}_error_runs'] += 1


def _truth_audit_trend_summary(daily_rows, days):
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

    days_without_any = max(days - days_with_any, 0)
    return {
        'days_with_any': days_with_any,
        'days_without_any': days_without_any,
        'days_with_full': days_with_full,
        'days_with_findings': days_with_findings,
        'days_with_warnings': days_with_warnings,
        'coverage_pct': _completion_pct(days_with_any, days),
        'full_coverage_pct': _completion_pct(days_with_full, days),
    }


def _recent_truth_audit_coverage_summary(now=None, days=7):
    now = now or timezone.now()
    days = max(int(days or 7), 1)
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    daily_rows = _daily_trend_rows(start, days)
    row_by_date = {row['date']: row for row in daily_rows}

    for run in U001SourceAuditRun.objects.filter(started_at__gte=start).order_by('started_at'):
        _apply_truth_audit_run(row_by_date=row_by_date, run=run, prefix='source_audit')
    for run in U001RD001ChainAuditRun.objects.filter(started_at__gte=start).order_by('started_at'):
        _apply_truth_audit_run(
            row_by_date=row_by_date,
            run=run,
            prefix='rd001_chain_audit',
            sample_key='transaction_count',
        )
    for run in U001FL001DerivedAuditRun.objects.filter(started_at__gte=start).order_by('started_at'):
        _apply_truth_audit_run(
            row_by_date=row_by_date,
            run=run,
            prefix='fl001_derived_audit',
            sample_key='candle_count',
        )

    summary = _truth_audit_trend_summary(daily_rows, days)
    state = 'healthy'
    headline = 'Recent truth coverage is present'
    detail = (
        f"{summary['days_with_any']}/{days} recent day(s) have at least one Phase 0 truth audit."
    )
    if summary['days_with_findings']:
        state = 'critical'
        headline = 'Recent truth audits found mismatches'
        detail = (
            f"{summary['days_with_findings']} recent day(s) produced truth-audit findings."
        )
    elif summary['days_with_any'] == 0:
        state = 'critical'
        headline = 'No recent truth-audit coverage'
        detail = f'No Phase 0 truth audits ran in the last {days} day(s).'
    elif summary['days_without_any'] or summary['days_with_warnings']:
        state = 'warn'
        headline = 'Recent truth coverage has gaps'
        detail = (
            f"{summary['days_without_any']} day(s) had no truth audit and "
            f"{summary['days_with_warnings']} day(s) produced warnings or audit execution issues."
        )

    summary.update({
        'state': state,
        'headline': headline,
        'detail': detail,
        'days': days,
    })
    return summary


def build_automation_history_summary(limit=50, action=None, status=None):
    """Return a dedicated summary of persisted U-001 automation ticks."""
    now = timezone.now()
    limit = max(min(int(limit or 50), 200), 1)

    state = _automation_summary()
    current_decision = select_next_action(
        U001AutomationState.objects.filter(singleton_key='u001').first()
        or U001AutomationState(singleton_key='u001'),
        collect_metrics(now=now),
    )
    queryset = U001AutomationTick.objects.all().order_by('-started_at', '-id')
    if action:
        queryset = queryset.filter(action=action)
    if status:
        queryset = queryset.filter(status=status)

    ticks = list(queryset[:limit])
    action_values = list(
        U001AutomationTick.objects.order_by()
        .values_list('action', flat=True)
        .distinct()
    )
    status_values = list(
        U001AutomationTick.objects.order_by()
        .values_list('status', flat=True)
        .distinct()
    )

    total_ticks = U001AutomationTick.objects.count()
    filtered_count = queryset.count()
    complete_count = queryset.filter(status='complete').count()
    error_count = queryset.filter(status='error').count()
    snapshot_count = queryset.filter(snapshot_taken=True).count()
    repair_count = queryset.filter(repaired_state=True).count()

    action_counts = Counter(
        queryset.values_list('action', flat=True)
    )
    status_counts = Counter(
        queryset.values_list('status', flat=True)
    )

    return {
        'generated_at': now,
        'state': state,
        'rd001_recent_runner': _rd001_recent_runner_summary(now),
        'connectivity_risk': _connectivity_risk_summary(now=now),
        'spin_risk': _automation_spin_summary(now=now),
        'truth_audit_lanes': _truth_audit_lane_rollups(now=now),
        'filters': {
            'action': action or '',
            'status': status or '',
            'limit': limit,
            'available_actions': action_values,
            'available_statuses': status_values,
        },
        'headline': {
            'total_ticks': total_ticks,
            'filtered_ticks': filtered_count,
            'complete_ticks': complete_count,
            'error_ticks': error_count,
            'snapshot_ticks': snapshot_count,
            'repair_ticks': repair_count,
        },
        'current_manual_equivalent': {
            'action': current_decision.action,
            'reason': current_decision.reason,
            'command': _manual_action_equivalent(current_decision.action, current_decision.kwargs),
        },
        'action_counts': [
            {'action': key, 'count': action_counts[key]}
            for key in sorted(action_counts.keys())
        ],
        'status_counts': [
            {'status': key, 'count': status_counts[key]}
            for key in sorted(status_counts.keys())
        ],
        'ticks': [
            {
                'started_at': tick.started_at,
                'completed_at': tick.completed_at,
                'action': tick.action,
                'reason': tick.reason,
                'status': tick.status,
                'command': tick.command,
                'command_kwargs': tick.command_kwargs,
                'result_summary': tick.result_summary,
                'repaired_state': tick.repaired_state,
                'snapshot_taken': tick.snapshot_taken,
                'notes': tick.notes,
                'age_label': _age_label(tick.started_at, now),
                'manual_command': _manual_action_equivalent(tick.action, tick.command_kwargs),
            }
            for tick in ticks
        ],
    }


def _truth_audit_lane_rollups(now=None):
    now = now or timezone.now()
    lane_defs = (
        (
            'truth_source_audit',
            'Live Source Audit Lane',
            _source_audit_summary(now),
        ),
        (
            'truth_rd001_chain_audit',
            'RD-001 Chain Audit Lane',
            _rd001_chain_audit_summary(now),
        ),
        (
            'truth_fl001_derived_audit',
            'FL-001 Derived Audit Lane',
            _fl001_derived_audit_summary(now),
        ),
    )

    rollups = []
    for action, label, audit_summary in lane_defs:
        ticks_qs = U001AutomationTick.objects.filter(action=action).order_by('-started_at', '-id')
        latest_tick = ticks_qs.first()
        total_ticks = ticks_qs.count()
        complete_ticks = ticks_qs.filter(status='complete').count()
        error_ticks = ticks_qs.filter(status='error').count()
        rollups.append({
            'action': action,
            'label': label,
            'total_ticks': total_ticks,
            'complete_ticks': complete_ticks,
            'error_ticks': error_ticks,
            'latest_tick_at': latest_tick.started_at if latest_tick else None,
            'latest_tick_status': latest_tick.status if latest_tick else 'not_started',
            'latest_tick_age_label': _age_label(latest_tick.started_at, now) if latest_tick else 'No data',
            'manual_command': _manual_action_equivalent(action, latest_tick.command_kwargs if latest_tick else {}),
            'audit': {
                'state': audit_summary['state'],
                'headline': audit_summary['headline'],
                'status': audit_summary['status'],
                'age_label': audit_summary['age_label'],
                'finding_count': audit_summary.get('finding_count', 0),
                'warning_count': audit_summary.get('warning_count', 0),
            },
        })
    return rollups


def _automation_spin_summary(now=None):
    now = now or timezone.now()
    action, streak = _latest_complete_streak()
    threshold = AUTOMATION_SPIN_STREAK_THRESHOLD
    if not streak:
        return {
            'state': 'warn',
            'headline': 'No completed automation streak yet',
            'detail': 'Spin risk cannot be evaluated until the controller has at least one complete tick.',
            'action': None,
            'streak_length': 0,
            'threshold': threshold,
            'structured_ticks': 0,
            'progress_label': 'No structured yield yet',
        }

    structured_ticks = sum(1 for tick in streak if tick.result_summary)
    metrics = collect_metrics(now=now)
    recent_mapping_pct = (
        metrics['recent_mapped_count'] / metrics['recent_discovered_count']
        if metrics['recent_discovered_count'] else 1.0
    )
    fl002_complete_pct = (
        metrics['fl002_complete_count'] / metrics['mature_count']
        if metrics['mature_count'] else 1.0
    )
    streak_len = len(streak)

    progress_label = 'No structured yield yet'
    state = 'healthy'
    headline = 'No active spin risk'
    detail = (
        f'The latest complete automation streak is {streak_len} {action} tick(s), '
        f'below the warning threshold of {threshold}.'
    )

    if action == 'rd001_recent':
        loaded = sum(_tick_result_count(tick, 'records_loaded') for tick in streak)
        progress_label = (
            f'{loaded} loaded rows across the streak'
            if structured_ticks == streak_len else
            f'Structured yield available for {structured_ticks}/{streak_len} ticks'
        )
        if streak_len >= threshold:
            if structured_ticks == streak_len and loaded == 0:
                state = 'critical'
                headline = 'RD-001 recent lane is spinning'
                detail = (
                    f'{streak_len} consecutive rd001_recent ticks completed without loading rows.'
                )
            elif structured_ticks == streak_len:
                state = 'warn'
                headline = 'RD-001 recent lane dominates automation'
                detail = (
                    f'{streak_len} consecutive rd001_recent ticks loaded {loaded} rows total.'
                )
            else:
                state = 'warn'
                headline = 'RD-001 recent streak needs more evidence'
                detail = (
                    f'{streak_len} consecutive rd001_recent ticks completed, but '
                    f'{streak_len - structured_ticks} older tick(s) do not have structured yield summaries.'
                )
    elif action == 'holders_catchup':
        loaded = sum(_tick_step_count(tick, 'holders', 'records_loaded') for tick in streak)
        succeeded = sum(_tick_step_count(tick, 'holders', 'succeeded') for tick in streak)
        progress_label = (
            f'{loaded} loaded holder rows across the streak'
            if structured_ticks == streak_len else
            f'Structured yield available for {structured_ticks}/{streak_len} ticks'
        )
        if streak_len >= threshold:
            if structured_ticks == streak_len and loaded == 0 and succeeded == 0:
                state = 'critical'
                headline = 'Holders catch-up is only skipping work'
                detail = (
                    f'{streak_len} consecutive holders_catchup ticks skipped all work while '
                    f'FL-002 mature coverage is still only {fl002_complete_pct:.1%}.'
                )
            elif structured_ticks == streak_len:
                state = 'warn'
                headline = 'Holders catch-up dominates automation'
                detail = (
                    f'{streak_len} consecutive holders_catchup ticks loaded {loaded} holder rows while '
                    f'FL-002 mature coverage is still only {fl002_complete_pct:.1%}.'
                )
            else:
                state = 'warn'
                headline = 'Holders streak needs more evidence'
                detail = (
                    f'{streak_len} consecutive holders_catchup ticks completed, but '
                    f'{streak_len - structured_ticks} older tick(s) do not have structured yield summaries.'
                )
    elif action == 'pool_mapping_recent':
        mapped = sum(_tick_step_count(tick, 'pool_mapping', 'mapped') for tick in streak)
        progress_label = (
            f'{mapped} mapped coins across the streak'
            if structured_ticks == streak_len else
            f'Structured yield available for {structured_ticks}/{streak_len} ticks'
        )
        if streak_len >= threshold:
            if structured_ticks == streak_len and mapped == 0:
                state = 'critical'
                headline = 'Recent pool mapping lane is spinning'
                detail = (
                    f'{streak_len} consecutive pool_mapping_recent ticks mapped nothing while '
                    f'recent mapping is still only {recent_mapping_pct:.1%}.'
                )
            elif structured_ticks == streak_len:
                state = 'warn'
                headline = 'Recent pool mapping dominates automation'
                detail = (
                    f'{streak_len} consecutive pool_mapping_recent ticks mapped {mapped} coins while '
                    f'recent mapping is still only {recent_mapping_pct:.1%}.'
                )
            else:
                state = 'warn'
                headline = 'Recent pool-mapping streak needs more evidence'
                detail = (
                    f'{streak_len} consecutive pool_mapping_recent ticks completed, but '
                    f'{streak_len - structured_ticks} older tick(s) do not have structured yield summaries.'
                )

    return {
        'state': state,
        'headline': headline,
        'detail': detail,
        'action': action,
        'streak_length': streak_len,
        'threshold': threshold,
        'structured_ticks': structured_ticks,
        'progress_label': progress_label,
    }


def _automation_summary():
    state = U001AutomationState.objects.filter(singleton_key='u001').first()
    recent_ticks = [
        {
            'started_at': tick.started_at,
            'completed_at': tick.completed_at,
            'action': tick.action,
            'status': tick.status,
            'reason': tick.reason,
            'result_summary': tick.result_summary,
            'snapshot_taken': tick.snapshot_taken,
            'repaired_state': tick.repaired_state,
            'notes': tick.notes,
        }
        for tick in U001AutomationTick.objects.order_by('-started_at', '-id')[:6]
    ]
    if not state:
        return {
            'state': 'warn',
            'headline': 'Controller has not run yet',
            'detail': 'No U-001 automation tick has written controller state yet.',
            'last_tick_at': None,
            'last_action': None,
            'last_action_reason': None,
            'last_action_status': 'not_started',
            'last_action_started_at': None,
            'last_action_completed_at': None,
            'last_snapshot_date': None,
            'consecutive_failures': 0,
            'guarded_attempts_today': 0,
            'notes': None,
            'recent_ticks': recent_ticks,
        }

    summary = {
        'state': 'healthy',
        'headline': 'Controller is active',
        'detail': 'The latest automation tick completed without an active circuit breaker.',
        'last_tick_at': state.last_tick_at,
        'last_action': state.last_action,
        'last_action_reason': state.last_action_reason,
        'last_action_status': state.last_action_status or 'unknown',
        'last_action_started_at': state.last_action_started_at,
        'last_action_completed_at': state.last_action_completed_at,
        'last_snapshot_date': state.last_snapshot_date,
        'consecutive_failures': state.consecutive_failures,
        'guarded_attempts_today': state.guarded_attempts_today,
        'notes': state.notes,
        'recent_ticks': recent_ticks,
    }

    if state.last_tick_at is None:
        summary.update({
            'state': 'warn',
            'headline': 'Controller state exists but no tick has run yet',
            'detail': 'The U-001 automation row is present, but no tick has completed.',
        })
    elif state.last_action_status == 'error':
        summary.update({
            'state': 'critical',
            'headline': 'Latest automation tick failed',
            'detail': 'The last selected lane raised an error and the controller stored the failure.',
        })
    elif state.last_action == 'no_action':
        summary.update({
            'state': 'warn',
            'headline': 'Controller intentionally idled',
            'detail': 'The latest tick held position because no lane beat the thresholds or a pause rule applied.',
        })
    elif state.consecutive_failures:
        summary.update({
            'state': 'warn',
            'headline': 'Controller recovered after recent failures',
            'detail': 'The latest tick completed, but the state still shows recent failure pressure.',
        })

    return summary


def _rd001_recent_runner_summary(now):
    path = status_path()
    raw = read_status_file(path)
    if raw is None:
        return {
            'state': 'warn',
            'headline': 'Dedicated recent RD-001 runner has not reported yet',
            'detail': 'No heartbeat file exists for the separate rolling recent-window maintainer.',
            'status': 'not_started',
            'pid': None,
            'pid_alive': False,
            'cycle': 0,
            'updated_at': None,
            'age_label': 'No data',
            'last_cycle_started_at': None,
            'last_cycle_completed_at': None,
            'last_exit_code': None,
            'sleep_seconds': None,
            'error_sleep_seconds': None,
            'current_log_file': None,
            'last_log_file': None,
            'status_file': str(path),
        }
    if raw.get('_error'):
        return {
            'state': 'critical',
            'headline': 'Dedicated recent RD-001 runner status could not be read',
            'detail': raw['_error'],
            'status': 'unknown',
            'pid': None,
            'pid_alive': False,
            'cycle': 0,
            'updated_at': None,
            'age_label': 'Unreadable',
            'last_cycle_started_at': None,
            'last_cycle_completed_at': None,
            'last_exit_code': None,
            'sleep_seconds': None,
            'error_sleep_seconds': None,
            'current_log_file': None,
            'last_log_file': None,
            'status_file': str(path),
        }

    updated_at = parse_runner_datetime(raw.get('updated_at'))
    current_state = raw.get('state') or 'unknown'
    pid = raw.get('pid')
    pid_alive_now = pid_alive(pid)
    cycle = int(raw.get('cycle') or 0)
    last_exit_code = raw.get('last_exit_code') or None

    summary = {
        'state': 'warn',
        'headline': 'Dedicated recent RD-001 runner heartbeat is present',
        'detail': 'The separate rolling recent-window maintainer has recorded status, but it is not clearly active.',
        'status': current_state,
        'pid': pid,
        'pid_alive': pid_alive_now,
        'cycle': cycle,
        'updated_at': updated_at,
        'age_label': _age_label(updated_at, now),
        'last_cycle_started_at': parse_runner_datetime(raw.get('last_cycle_started_at')),
        'last_cycle_completed_at': parse_runner_datetime(raw.get('last_cycle_completed_at')),
        'last_exit_code': last_exit_code,
        'sleep_seconds': raw.get('sleep_seconds') or None,
        'error_sleep_seconds': raw.get('error_sleep_seconds') or None,
        'current_log_file': raw.get('current_log_file') or None,
        'last_log_file': raw.get('last_log_file') or None,
        'status_file': str(path),
    }

    if pid_alive_now and current_state in {'starting', 'running', 'sleeping', 'cycle_error'}:
        summary.update({
            'state': 'healthy' if current_state != 'cycle_error' else 'warn',
            'headline': (
                'Dedicated recent RD-001 runner is active'
                if current_state in {'starting', 'running'}
                else 'Dedicated recent RD-001 runner is sleeping between cycles'
                if current_state == 'sleeping'
                else 'Dedicated recent RD-001 runner recovered from a failed cycle'
            ),
            'detail': (
                'The separate rolling recent-window maintainer process is alive and updating its heartbeat.'
                if current_state != 'cycle_error'
                else 'The runner is still alive, but the latest cycle failed and it is backing off before the next attempt.'
            ),
        })
        return summary

    if current_state == 'stopped':
        summary.update({
            'state': 'warn',
            'headline': 'Dedicated recent RD-001 runner is stopped',
            'detail': 'A runner status file exists, but the process is no longer alive.',
        })
        return summary

    if last_exit_code and last_exit_code != '0':
        summary.update({
            'state': 'critical',
            'headline': 'Dedicated recent RD-001 runner exited after a failed cycle',
            'detail': 'The last recorded runner heartbeat shows a non-zero exit code and no live process.',
        })
        return summary

    return summary


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


def _connectivity_risk_summary(now=None):
    now = now or timezone.now()
    latest_tick = (
        U001AutomationTick.objects.exclude(action='no_action')
        .order_by('-started_at', '-id')
        .first()
    )
    latest_boot_recovery = U001BootRecoveryRun.objects.order_by('-started_at', '-id').first()
    streak = _recent_connectivity_error_streak()
    runner_raw = read_status_file(status_path())
    recovery_at = last_successful_cycle_at(runner_raw)
    latest_issue_at = _latest_connectivity_issue_at(latest_tick, latest_boot_recovery, streak)

    summary = {
        'state': 'healthy',
        'headline': 'No active connectivity signal',
        'detail': 'The latest automation evidence does not suggest an internet or upstream reachability outage.',
        'latest_action': latest_tick.action if latest_tick else None,
        'latest_tick_at': latest_tick.started_at if latest_tick else None,
        'latest_note': latest_tick.notes if latest_tick else None,
        'streak_length': len(streak),
        'latest_boot_recovery_status': latest_boot_recovery.status if latest_boot_recovery else None,
        'latest_issue_at': latest_issue_at,
        'recovered_at': recovery_at,
        'recovered': bool(recovery_at and latest_issue_at and recovery_at > latest_issue_at),
    }

    if summary['recovered']:
        summary.update({
            'headline': 'Recovered after recent outage',
            'detail': (
                'The dedicated recent RD-001 runner completed a successful cycle after the latest transport or '
                'reachability failure, so network-dependent ingestion appears to be back.'
            ),
            'latest_note': latest_tick.notes if latest_tick and latest_tick.status == 'error' else None,
        })
        return summary

    if streak:
        summary.update({
            'state': 'critical' if len(streak) >= 2 else 'warn',
            'headline': (
                'Internet or upstream reachability looks impaired'
                if len(streak) >= 2 else
                'Latest automation failure looks like a connectivity issue'
            ),
            'detail': (
                f'{len(streak)} consecutive automation tick(s) failed with transport or reachability errors. '
                'Local services may still be up, but network-dependent U-001 lanes are likely blocked until connectivity returns.'
            ),
            'latest_action': streak[0].action,
            'latest_tick_at': streak[0].started_at,
            'latest_note': streak[0].notes,
        })
        return summary

    if latest_tick and latest_tick.status == 'error' and _looks_like_connectivity_issue(latest_tick.notes):
        summary.update({
            'state': 'warn',
            'headline': 'Latest automation failure looks like a connectivity issue',
            'detail': (
                'The most recent automation tick failed with a transport or reachability error. '
                'If this repeats, the controller will pause instead of hammering remote providers.'
            ),
        })
        return summary

    if (
        latest_boot_recovery
        and latest_boot_recovery.status == 'error'
        and _looks_like_connectivity_issue(latest_boot_recovery.notes)
    ):
        summary.update({
            'state': 'warn',
            'headline': 'Latest boot recovery failed on reachability',
            'detail': (
                'The latest post-boot recovery reached local services, but the first automation tick failed with a transport or reachability error.'
            ),
            'latest_note': latest_boot_recovery.notes,
        })
    return summary


def _boot_recovery_summary(now):
    run = U001BootRecoveryRun.objects.order_by('-started_at', '-id').first()
    if not run:
        return {
            'state': 'warn',
            'headline': 'No reboot recovery recorded yet',
            'detail': 'The local machine has not recorded a post-boot U-001 recovery run yet.',
            'started_at': None,
            'completed_at': None,
            'status': 'not_started',
            'age_label': 'No data',
            'db_reachable': False,
            'migrations_ok': False,
            'automation_tick_started': False,
            'automation_tick_status': None,
            'log_path': None,
            'notes': None,
        }

    state = 'healthy'
    headline = 'Latest reboot recovery completed'
    detail = 'The latest recorded local reboot recovery reached the automation tick successfully.'
    if run.status == 'error':
        state = 'critical'
        headline = 'Latest reboot recovery failed'
        detail = run.notes or 'The reboot recovery run failed after database startup.'
    elif run.automation_tick_status != 'complete':
        state = 'warn'
        headline = 'Latest reboot recovery was incomplete'
        detail = 'The reboot recovery reached the database, but the automation tick did not complete cleanly.'

    return {
        'state': state,
        'headline': headline,
        'detail': detail,
        'started_at': run.started_at,
        'completed_at': run.completed_at,
        'status': run.status,
        'age_label': _age_label(run.completed_at or run.started_at, now),
        'db_reachable': run.db_reachable,
        'migrations_ok': run.migrations_ok,
        'automation_tick_started': run.automation_tick_started,
        'automation_tick_status': run.automation_tick_status,
        'log_path': run.log_path,
        'notes': run.notes,
    }


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


def _tick_result_count(tick, key):
    summary = tick.result_summary or {}
    value = summary.get(key, 0)
    return value if isinstance(value, int) else 0


def _tick_step_count(tick, step_name, key):
    summary = tick.result_summary or {}
    steps = summary.get('steps') or {}
    step = steps.get(step_name) or {}
    value = step.get(key, 0)
    return value if isinstance(value, int) else 0


def _source_audit_summary(now):
    run = U001SourceAuditRun.objects.order_by('-started_at', '-id').first()
    if not run:
        return {
            'state': 'warn',
            'headline': 'No live source audit recorded yet',
            'detail': 'Run audit_u001_sources at least once so the cockpit can compare warehouse state against upstream providers.',
            'started_at': None,
            'completed_at': None,
            'status': 'not_started',
            'age_label': 'No data',
            'finding_count': 0,
            'warning_count': 0,
            'highlights': [],
            'top_findings': [],
            'top_warnings': [],
        }

    state = 'healthy'
    headline = 'Latest live source audit passed'
    detail = 'The most recent sampled provider check found no mismatches or lag findings.'
    if run.status == 'error':
        state = 'critical'
        headline = 'Latest live source audit failed to run'
        detail = run.notes or 'The command raised an execution error before completing all source checks.'
    elif run.status == 'finding':
        state = 'critical'
        headline = 'Latest live source audit found mismatches'
        detail = 'At least one sampled upstream comparison disagreed with local warehouse state.'
    elif run.status == 'warning':
        state = 'warn'
        headline = 'Latest live source audit was low-confidence'
        detail = 'The audit completed, but some sample lanes returned warnings or low-information coverage.'

    summary = run.summary or {}
    discovery = summary.get('discovery') or {}
    layers = summary.get('layers') or {}
    highlights = []
    if discovery:
        highlights.append({
            'label': 'Discovery status',
            'value': discovery.get('status', 'unknown'),
        })
        if discovery.get('lag_hours') is not None:
            highlights.append({
                'label': 'Discovery lag hours',
                'value': f"{discovery['lag_hours']:.1f}",
            })
    for key, label in (('fl001', 'FL-001'), ('fl002', 'FL-002'), ('rd001', 'RD-001')):
        rows = layers.get(key) or []
        informative = sum(
            1 for row in rows if row.get('status') in {'ok', 'finding'}
        )
        if rows or informative:
            highlights.append({
                'label': f'{label} informative samples',
                'value': informative,
            })

    return {
        'state': state,
        'headline': headline,
        'detail': detail,
        'started_at': run.started_at,
        'completed_at': run.completed_at,
        'status': run.status,
        'age_label': _age_label(run.completed_at or run.started_at, now),
        'finding_count': run.finding_count,
        'warning_count': run.warning_count,
        'highlights': highlights,
        'top_findings': (summary.get('findings') or [])[:3],
        'top_warnings': (summary.get('warnings') or [])[:3],
    }


def _rd001_chain_audit_summary(now):
    run = U001RD001ChainAuditRun.objects.order_by('-started_at', '-id').first()
    if not run:
        return {
            'state': 'warn',
            'headline': 'No RD-001 chain audit recorded yet',
            'detail': 'Run audit_u001_rd001_chain so the cockpit can compare sampled RD-001 rows against direct Solana RPC.',
            'started_at': None,
            'completed_at': None,
            'status': 'not_started',
            'age_label': 'No data',
            'coin_count': 0,
            'transaction_count': 0,
            'finding_count': 0,
            'warning_count': 0,
            'rpc_url': None,
            'highlights': [],
            'top_findings': [],
            'top_warnings': [],
        }

    state = 'healthy'
    headline = 'Latest RD-001 chain audit passed'
    detail = 'The most recent sampled direct-RPC RD-001 check found no mismatches.'
    if run.status == 'error':
        state = 'critical'
        headline = 'Latest RD-001 chain audit failed to run'
        detail = run.notes or 'The direct-RPC chain audit raised an execution error.'
    elif run.status == 'finding':
        state = 'critical'
        headline = 'Latest RD-001 chain audit found mismatches'
        detail = 'The latest sampled direct-RPC check disagreed with stored RD-001 rows.'
    elif run.status == 'warning':
        state = 'warn'
        headline = 'Latest RD-001 chain audit was partial'
        detail = 'The direct-RPC chain audit completed, but some sample windows were low-confidence or rate-limited.'

    summary = run.summary or {}
    row_aggregate = summary.get('aggregate') or {}
    window_aggregate = summary.get('window_aggregate') or {}
    options = run.options or {}
    highlights = [
        {'label': 'Sampled coins', 'value': run.coin_count},
        {'label': 'Sampled transactions', 'value': run.transaction_count},
    ]
    row_statuses = row_aggregate.get('statuses') or {}
    if row_statuses:
        highlights.append({
            'label': 'Row matches',
            'value': row_statuses.get('ok', 0),
        })
    window_warnings = window_aggregate.get('warning_buckets') or {}
    if window_warnings:
        if window_warnings.get('window_scan_failed'):
            highlights.append({
                'label': 'Window scan failures',
                'value': window_warnings['window_scan_failed'],
            })
        if window_warnings.get('ambiguous_pool_signatures'):
            highlights.append({
                'label': 'Ambiguous pool signatures',
                'value': window_warnings['ambiguous_pool_signatures'],
            })

    return {
        'state': state,
        'headline': headline,
        'detail': detail,
        'started_at': run.started_at,
        'completed_at': run.completed_at,
        'status': run.status,
        'age_label': _age_label(run.completed_at or run.started_at, now),
        'coin_count': run.coin_count,
        'transaction_count': run.transaction_count,
        'finding_count': run.finding_count,
        'warning_count': run.warning_count,
        'rpc_url': options.get('rpc_url'),
        'rpc_source': options.get('rpc_source'),
        'highlights': highlights[:4],
        'top_findings': (summary.get('findings') or [])[:3],
        'top_warnings': (summary.get('warnings') or [])[:3],
    }


def _fl001_derived_audit_summary(now):
    run = U001FL001DerivedAuditRun.objects.order_by('-started_at', '-id').first()
    if not run:
        return {
            'state': 'warn',
            'headline': 'No FL-001 derived audit recorded yet',
            'detail': 'Run audit_u001_fl001_derived so the cockpit can compare stored FL-001 candles against candles derived from warehouse RD-001.',
            'started_at': None,
            'completed_at': None,
            'status': 'not_started',
            'age_label': 'No data',
            'coin_count': 0,
            'candle_count': 0,
            'finding_count': 0,
            'warning_count': 0,
            'highlights': [],
            'top_findings': [],
            'top_warnings': [],
        }

    state = 'healthy'
    headline = 'Latest FL-001 derived audit passed'
    detail = 'The latest sampled candle derivation matched stored FL-001 rows.'
    if run.status == 'error':
        state = 'critical'
        headline = 'Latest FL-001 derived audit failed to run'
        detail = run.notes or 'The derived candle audit raised an execution error.'
    elif run.status == 'finding':
        state = 'critical'
        headline = 'Latest FL-001 derived audit found mismatches'
        detail = 'The latest sampled derived-candle check disagreed with stored FL-001 rows.'
    elif run.status == 'warning':
        state = 'warn'
        headline = 'Latest FL-001 derived audit was partial'
        detail = 'The derived candle audit completed, but some sampled windows drifted or had missing prerequisites.'

    summary = run.summary or {}
    aggregate = summary.get('aggregate') or {}
    highlights = [
        {'label': 'Sampled coins', 'value': run.coin_count},
        {'label': 'Derived candles', 'value': run.candle_count},
    ]
    statuses = aggregate.get('statuses') or {}
    if statuses:
        highlights.append({
            'label': 'Matched windows',
            'value': statuses.get('ok', 0),
        })
    warning_buckets = aggregate.get('warning_buckets') or {}
    if warning_buckets.get('candle_value_drift'):
        highlights.append({
            'label': 'Drift warnings',
            'value': warning_buckets['candle_value_drift'],
        })
    elif warning_buckets.get('missing_sol_oracle_minutes'):
        highlights.append({
            'label': 'Missing SOL minutes',
            'value': warning_buckets['missing_sol_oracle_minutes'],
        })

    return {
        'state': state,
        'headline': headline,
        'detail': detail,
        'started_at': run.started_at,
        'completed_at': run.completed_at,
        'status': run.status,
        'age_label': _age_label(run.completed_at or run.started_at, now),
        'coin_count': run.coin_count,
        'candle_count': run.candle_count,
        'finding_count': run.finding_count,
        'warning_count': run.warning_count,
        'highlights': highlights[:4],
        'top_findings': (summary.get('findings') or [])[:3],
        'top_warnings': (summary.get('warnings') or [])[:3],
    }


def _age_label(timestamp, now):
    if not timestamp:
        return 'No data'
    age_seconds = max((now - timestamp).total_seconds(), 0)
    if age_seconds < 3600:
        minutes = max(int(age_seconds // 60), 0)
        return f'{minutes}m old'
    if age_seconds < 86400:
        return f'{age_seconds / 3600:.1f}h old'
    return f'{age_seconds / 86400:.1f}d old'


def _freshness_state(timestamp, now, warn_after, critical_after):
    if not timestamp:
        return 'critical'
    age = now - timestamp
    if age >= critical_after:
        return 'critical'
    if age >= warn_after:
        return 'warn'
    return 'healthy'


def _freshness_item(label, timestamp, now, warn_after, critical_after):
    return {
        'label': label,
        'timestamp': timestamp,
        'age_label': _age_label(timestamp, now),
        'state': _freshness_state(timestamp, now, warn_after, critical_after),
    }


def _eligible_count(layer_id, total_coins, mapped_count):
    if layer_id in {'FL-001', 'RD-001'}:
        return mapped_count
    return total_coins


def _status_counts(layer_id):
    rows = U001PipelineStatus.objects.filter(layer_id=layer_id).values_list('status', flat=True)
    counts = Counter(rows)
    ordered = {}
    for status in STATUS_ORDER:
        if counts.get(status):
            ordered[status] = counts[status]
    return ordered


def _error_bucket_counts(layer_id):
    rows = (
        U001PipelineStatus.objects.filter(
            layer_id=layer_id,
            last_error__isnull=False,
        ).values_list('last_error', flat=True)
    )
    counts = Counter()
    for error in rows:
        bucket = classify_error_bucket(error)
        if bucket:
            counts[bucket] += 1
    ordered = {}
    for bucket in ERROR_BUCKET_ORDER:
        if counts.get(bucket):
            ordered[bucket] = counts[bucket]
    return ordered


def _completion_pct(numerator, denominator):
    if not denominator:
        return 0.0
    return round((numerator / denominator) * 100, 1)


def _min_aware_datetime():
    return datetime.min.replace(tzinfo=dt_timezone.utc)


def _coverage_scope(preset, now):
    key = preset if preset in COVERAGE_PRESETS else '1000'
    mode, value, label = COVERAGE_PRESETS[key]
    if mode == 'count':
        coins = list(
            MigratedCoin.objects.exclude(anchor_event__isnull=True)
            .order_by('-anchor_event')[:value]
        )
    else:
        cutoff = now - timedelta(days=value)
        coins = list(
            MigratedCoin.objects.filter(anchor_event__gte=cutoff)
            .order_by('-anchor_event')
        )

    coin_ids = [coin.mint_address for coin in coins]
    anchors = [coin.anchor_event for coin in coins if coin.anchor_event]
    return {
        'preset': key,
        'label': label,
        'coin_ids': coin_ids,
        'range_start': min(anchors) if anchors else None,
        'range_end': max(anchors) if anchors else None,
    }


def _coverage_preset_list(active):
    items = []
    for key, (_, _, label) in COVERAGE_PRESETS.items():
        items.append({
            'key': key,
            'label': label,
            'active': key == active,
        })
    return items


def _distinct_count(model, coin_ids):
    return model.objects.filter(coin_id__in=coin_ids).values('coin_id').distinct().count()


def _status_stage_count(layer_id, coin_ids, status=None):
    qs = U001PipelineStatus.objects.filter(
        coin_id__in=coin_ids,
        layer_id=layer_id,
    )
    if status:
        qs = qs.filter(status=status)
    return qs.count()


def _coverage_bottleneck(stages, coin_ids):
    if not stages:
        return {
            'title': 'No discovery rows in this slice',
            'detail': 'The selected slice has no discovered U-001 coins yet.',
        }

    drops = [stage for stage in stages[1:] if stage['delta_from_prior'] > 0]
    if not drops:
        return {
            'title': 'No obvious drop-off in this slice',
            'detail': 'The current funnel stages are flat, so this slice does not show a single dominant dropout point.',
        }

    biggest = max(drops, key=lambda stage: stage['delta_from_prior'])
    if biggest['key'] == 'mapped':
        return {
            'title': 'Pool mapping is the main bottleneck',
            'detail': 'Most of the discovered coins in this slice have not been mapped to pools yet, which caps every downstream layer.',
        }

    fl002_auth = U001PipelineStatus.objects.filter(
        coin_id__in=coin_ids,
        layer_id='FL-002',
        last_error__isnull=False,
    )
    fl002_auth_count = sum(
        1 for error in fl002_auth.values_list('last_error', flat=True)
        if classify_error_bucket(error) == 'auth'
    )
    if biggest['key'].startswith('fl002') and fl002_auth_count:
        return {
            'title': 'FL-002 auth failures are visible',
            'detail': 'Holder coverage is being constrained by current authentication failures instead of just normal backlog.',
        }

    if biggest['key'] == 'rd001_complete':
        return {
            'title': 'RD-001 partial backlog is the main drop-off',
            'detail': 'Many coins have entered the RD-001 lane, but too few of them are window_complete yet.',
        }

    return {
        'title': f'{biggest["label"]} is the largest drop-off',
        'detail': f'This stage drops {biggest["delta_from_prior"]} coins versus the previous funnel step.',
    }


def _coin_layer_status(layer_id, status_rows):
    row = status_rows.get(layer_id) or {}
    return {
        'layer_id': layer_id,
        'title': LAYER_TITLES.get(layer_id, layer_id),
        'status': row.get('status', 'not_started'),
        'watermark': row.get('watermark'),
        'last_run_at': row.get('last_run_at'),
        'last_error': row.get('last_error'),
    }


def _coin_timeline(coin):
    points = [
        {
            'label': 'Discovered',
            'timestamp': coin.ingested_at,
            'detail': 'Coin row first landed in MigratedCoin.',
        },
    ]

    first_pool = PoolMapping.objects.filter(coin_id=coin.mint_address).aggregate(
        discovered_at=Min('discovered_at'),
    )['discovered_at']
    if first_pool:
        points.append({
            'label': 'Mapped',
            'timestamp': first_pool,
            'detail': 'First pool mapping was discovered.',
        })

    first_fl001 = OHLCVCandle.objects.filter(coin_id=coin.mint_address).aggregate(
        first=Min('ingested_at'),
    )['first']
    if first_fl001:
        points.append({
            'label': 'First FL-001 load',
            'timestamp': first_fl001,
            'detail': 'First OHLCV row arrived.',
        })

    first_fl002 = HolderSnapshot.objects.filter(coin_id=coin.mint_address).aggregate(
        first=Min('ingested_at'),
    )['first']
    if first_fl002:
        points.append({
            'label': 'First FL-002 load',
            'timestamp': first_fl002,
            'detail': 'First holder snapshot arrived.',
        })

    first_rd001 = RawTransaction.objects.filter(coin_id=coin.mint_address).aggregate(
        first=Min('ingested_at'),
    )['first']
    if first_rd001:
        points.append({
            'label': 'First RD-001 load',
            'timestamp': first_rd001,
            'detail': 'First raw transaction row arrived.',
        })

    most_recent_run = U001PipelineRun.objects.filter(
        coin_id=coin.mint_address,
    ).aggregate(
        latest=Max('started_at'),
    )['latest']
    if most_recent_run:
        points.append({
            'label': 'Most recent run',
            'timestamp': most_recent_run,
            'detail': 'Latest U-001 pipeline run started for this coin.',
        })

    return sorted(points, key=lambda item: item['timestamp'] or _min_aware_datetime())


def _rd001_status_rows():
    rows = U001PipelineStatus.objects.filter(layer_id='RD-001').values(
        'coin_id',
        'status',
        'watermark',
        'last_run_at',
        'last_error',
    )
    return {
        row['coin_id']: row
        for row in rows
    }


def _is_free_tier_guarded(error):
    return FREE_TIER_GUARD_TEXT in (error or '').lower()


def _guard_count(error):
    match = re.search(r'Filtered signature count (\d+)', error or '')
    if match:
        return int(match.group(1))
    return None


def _guarded_item(queue_item):
    item = dict(queue_item)
    item['guard_count'] = _guard_count(item.get('last_error'))
    return item


def _daily_trend_rows(start, days):
    rows = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        rows.append({
            'date': day,
            'label': day.isoformat(),
            'batch_started': 0,
            'batch_complete': 0,
            'batch_error': 0,
            'coins_succeeded': 0,
            'coins_failed': 0,
            'batch_api_calls': 0,
            'has_snapshot': False,
            'discovered_count': None,
            'mapped_count': None,
            'fl001_complete_count': None,
            'fl002_complete_count': None,
            'rd001_complete_count': None,
            'rd001_partial_count': None,
            'rd001_error_count': None,
            'rd001_transport_error_count': None,
            'rd001_guard_error_count': None,
            'fl002_auth_error_count': None,
            'fl001_complete_runs': 0,
            'fl001_error_runs': 0,
            'fl001_records_loaded': 0,
            'fl001_api_calls': 0,
            'fl002_complete_runs': 0,
            'fl002_error_runs': 0,
            'fl002_records_loaded': 0,
            'fl002_api_calls': 0,
            'fl002_auth_errors': 0,
            'rd001_complete_runs': 0,
            'rd001_error_runs': 0,
            'rd001_records_loaded': 0,
            'rd001_api_calls': 0,
            'rd001_transport_errors': 0,
            'rd001_guard_errors': 0,
            'source_audit_runs': 0,
            'source_audit_ok': 0,
            'source_audit_warning_runs': 0,
            'source_audit_finding_runs': 0,
            'source_audit_error_runs': 0,
            'source_audit_findings': 0,
            'source_audit_warnings': 0,
            'rd001_chain_audit_runs': 0,
            'rd001_chain_audit_ok': 0,
            'rd001_chain_audit_warning_runs': 0,
            'rd001_chain_audit_finding_runs': 0,
            'rd001_chain_audit_error_runs': 0,
            'rd001_chain_audit_findings': 0,
            'rd001_chain_audit_warnings': 0,
            'rd001_chain_audit_sampled': 0,
            'fl001_derived_audit_runs': 0,
            'fl001_derived_audit_ok': 0,
            'fl001_derived_audit_warning_runs': 0,
            'fl001_derived_audit_finding_runs': 0,
            'fl001_derived_audit_error_runs': 0,
            'fl001_derived_audit_findings': 0,
            'fl001_derived_audit_warnings': 0,
            'fl001_derived_audit_sampled': 0,
        })
    return rows


def _trend_prefix(layer_id):
    return {
        'FL-001': 'fl001',
        'FL-002': 'fl002',
        'RD-001': 'rd001',
    }.get(layer_id)


def _discovery_card(total_coins, mature_count, latest_ingested):
    return {
        'id': 'discovery',
        'title': 'Discovery',
        'eligible_count': total_coins,
        'with_data_count': total_coins,
        'window_complete_count': total_coins,
        'partial_count': 0,
        'error_count': 0,
        'in_progress_count': 0,
        'complete_pct': 100.0 if total_coins else 0.0,
        'meta': f'Mature coins: {mature_count}',
        'latest_ingested': latest_ingested,
    }


def _pool_mapping_card(total_coins, mapped_count):
    unmapped_count = max(total_coins - mapped_count, 0)
    return {
        'id': 'pool-mapping',
        'title': 'Pool Mapping',
        'eligible_count': total_coins,
        'with_data_count': mapped_count,
        'window_complete_count': mapped_count,
        'partial_count': 0,
        'error_count': 0,
        'in_progress_count': 0,
        'complete_pct': _completion_pct(mapped_count, total_coins),
        'meta': f'Unmapped coins: {unmapped_count}',
        'latest_ingested': None,
    }


def _layer_card(layer_id, model, eligible_count):
    latest_ingested = model.objects.aggregate(v=Max('ingested_at'))['v']
    data_coin_count = model.objects.values('coin_id').distinct().count()
    status_counts = _status_counts(layer_id)
    return {
        'id': layer_id.lower(),
        'title': LAYER_TITLES[layer_id],
        'eligible_count': eligible_count,
        'with_data_count': data_coin_count,
        'window_complete_count': status_counts.get('window_complete', 0),
        'partial_count': status_counts.get('partial', 0),
        'error_count': status_counts.get('error', 0),
        'in_progress_count': status_counts.get('in_progress', 0),
        'complete_pct': _completion_pct(
            status_counts.get('window_complete', 0),
            eligible_count,
        ),
        'meta': f"Status rows: {sum(status_counts.values())}",
        'latest_ingested': latest_ingested,
    }


def _serialize_batch_run(batch, now):
    finished_at = batch.completed_at or now
    elapsed = max((finished_at - batch.started_at).total_seconds(), 0)
    if elapsed < 60:
        elapsed_label = f'{int(elapsed)}s'
    elif elapsed < 3600:
        elapsed_label = f'{elapsed / 60:.1f}m'
    else:
        elapsed_label = f'{elapsed / 3600:.1f}h'
    return {
        'id': batch.id,
        'mode': batch.mode,
        'status': batch.status,
        'started_at': batch.started_at,
        'completed_at': batch.completed_at,
        'coins_succeeded': batch.coins_succeeded,
        'coins_failed': batch.coins_failed,
        'api_calls': batch.api_calls,
        'elapsed_label': elapsed_label,
        'error_message': batch.error_message,
    }


def _recent_coverage_snapshot(limit=1000):
    recent_coin_ids = list(
        MigratedCoin.objects.exclude(anchor_event__isnull=True)
        .order_by('-anchor_event')
        .values_list('mint_address', flat=True)[:limit]
    )
    discovered_count = len(recent_coin_ids)
    if not recent_coin_ids:
        return {
            'label': f'Last {limit} discovered coins',
            'discovered_count': 0,
            'mapped_count': 0,
            'rd001_status_count': 0,
            'rd001_complete_count': 0,
        }

    return {
        'label': f'Last {discovered_count} discovered coins',
        'discovered_count': discovered_count,
        'mapped_count': PoolMapping.objects.filter(
            coin_id__in=recent_coin_ids,
        ).values('coin_id').distinct().count(),
        'rd001_status_count': U001PipelineStatus.objects.filter(
            coin_id__in=recent_coin_ids,
            layer_id='RD-001',
        ).count(),
        'rd001_complete_count': U001PipelineStatus.objects.filter(
            coin_id__in=recent_coin_ids,
            layer_id='RD-001',
            status='window_complete',
        ).count(),
    }


def _recommended_action(
    now,
    latest_coin_ingested,
    stale_cutoff,
    total_coins,
    mapped_count,
    rd001_card,
    rd001_error_buckets,
    recent_coverage,
):
    state = U001AutomationState.objects.filter(singleton_key='u001').first()
    decision = select_next_action(
        state or U001AutomationState(singleton_key='u001'),
        collect_metrics(now=now),
    )
    controller_recommendation = _controller_recommendation(decision)
    if controller_recommendation:
        return controller_recommendation

    if latest_coin_ingested is None or latest_coin_ingested < stale_cutoff:
        return {
            'title': 'Refresh discovery first',
            'detail': 'The discovery layer is stale, so downstream coverage decisions are operating on old universe membership.',
            'action': 'Run U-001 discovery before spending more effort on downstream recovery.',
            'source': 'heuristic',
        }

    recent_discovered = recent_coverage['discovered_count']
    recent_mapped = recent_coverage['mapped_count']
    if recent_discovered and (recent_mapped / recent_discovered) < 0.25:
        return {
            'title': 'Pool mapping is the bottleneck',
            'detail': 'Recent discovery is present, but too few of those coins are mapped to pools for downstream layers to run.',
            'action': 'Prioritize pool-mapping coverage before pushing more RD-001 backfill.',
            'source': 'heuristic',
        }

    if rd001_card['partial_count'] > rd001_card['window_complete_count']:
        return {
            'title': 'Run historical RD-001 partial recovery',
            'detail': 'The RD-001 backlog is still dominated by partial windows, so each clean Helius slice improves usable coverage directly.',
            'action': 'Run a small `run_batch_partials_historical.sh --max-coins 3` slice.',
            'source': 'heuristic',
        }

    if rd001_error_buckets.get('transport'):
        return {
            'title': 'Chip away at recent Shyft transport residue',
            'detail': 'Transport failures remain visible in current RD-001 statuses, so recent safe steady-state runs are still worth validating.',
            'action': 'Run a safe recent RD-001 steady-state slice and watch the transport bucket.',
            'source': 'heuristic',
        }

    if total_coins and mapped_count < total_coins:
        return {
            'title': 'Close the mapping gap',
            'detail': 'Unmapped U-001 coins still cap downstream FL-001 and RD-001 coverage.',
            'action': 'Continue pool-mapping recovery on recent discovery cohorts.',
            'source': 'heuristic',
        }

    return {
        'title': 'System is readable',
        'detail': 'No obvious top-level blocker dominates the current snapshot.',
        'action': 'Use coverage and queue views next to choose a targeted recovery lane.',
        'source': 'heuristic',
    }


def _controller_recommendation(decision):
    reason = decision.reason or ''
    lowered = reason.lower()

    if decision.action == 'refresh_core':
        if 'pool mapping coverage' in lowered:
            return {
                'title': 'Pool mapping is the bottleneck',
                'detail': reason,
                'action': 'Let automation run the core refresh lane before spending more effort downstream.',
                'source': 'controller',
            }
        return {
            'title': 'Refresh discovery first',
            'detail': reason,
            'action': 'Let automation run the core refresh lane before spending more effort downstream.',
            'source': 'controller',
        }

    if decision.action == 'truth_source_audit':
        return {
            'title': 'Recent live-source truth coverage is stale',
            'detail': reason,
            'action': 'Let automation refresh the low-budget provider-source audit before trusting the latest Phase 0 status.',
            'source': 'controller',
        }

    if decision.action == 'pool_mapping_recent':
        return {
            'title': 'Recent pool mapping needs dedicated catch-up',
            'detail': reason,
            'action': 'Let automation spend the next tick on recent unmapped coins before expecting recent RD-001 coverage.',
            'source': 'controller',
        }

    if decision.action == 'holders_catchup':
        return {
            'title': 'FL-002 mature coverage is behind',
            'detail': reason,
            'action': 'Let automation spend the next tick on holder catch-up instead of RD-001 work.',
            'source': 'controller',
        }

    if decision.action == 'rd001_recent':
        return {
            'title': 'Run the recent RD-001 safe lane',
            'detail': reason,
            'action': 'Use the recent safe Shyft lane and watch whether transport residue keeps falling.',
            'source': 'controller',
        }

    if decision.action == 'truth_rd001_chain_audit':
        return {
            'title': 'Refresh RD-001 direct chain truth',
            'detail': reason,
            'action': 'Let automation run the low-budget direct-RPC RD-001 audit before relying on stale chain-parity results.',
            'source': 'controller',
        }

    if decision.action == 'truth_fl001_derived_audit':
        return {
            'title': 'Refresh FL-001 self-derived truth',
            'detail': reason,
            'action': 'Let automation re-derive sampled candles from warehouse RD-001 before trusting stale FL-001 parity.',
            'source': 'controller',
        }

    if decision.action == 'rd001_partial_historical':
        return {
            'title': 'Run historical RD-001 partial recovery',
            'detail': reason,
            'action': 'Spend the next tick on a small historical Helius partial slice.',
            'source': 'controller',
        }

    if decision.action == 'rd001_error_recovery':
        return {
            'title': 'Run controlled RD-001 error recovery',
            'detail': reason,
            'action': 'Use the error lane on its scheduled retry cadence instead of widening scope.',
            'source': 'controller',
        }

    if decision.action == 'rd001_guarded':
        return {
            'title': 'Spend guarded Helius budget deliberately',
            'detail': reason,
            'action': 'Use the one-off guarded lane only for the smallest known overage.',
            'source': 'controller',
        }

    if decision.action == 'no_action' and (
        'cooldown' in lowered
        or 'pause' in lowered
        or 'thresholds' in lowered
    ):
        return {
            'title': 'Automation is paused by policy',
            'detail': reason,
            'action': 'Hold position or force a lane manually only after reviewing the last failure and controller state.',
            'source': 'controller',
        }

    return None


def _manual_action_equivalent(action, kwargs):
    kwargs = kwargs or {}
    if action == 'refresh_core':
        command = './scripts/manage.sh orchestrate --universe u001 --steps discovery,pool_mapping,ohlcv'
        if kwargs.get('days'):
            command += f" --days {kwargs['days']}"
        if kwargs.get('coins'):
            command += f" --coins {kwargs['coins']}"
        if kwargs.get('mature_only'):
            command += " --mature-only"
        return command

    if action == 'truth_source_audit':
        return './scripts/manage.sh audit_u001_sources'

    if action == 'pool_mapping_recent':
        command = './scripts/manage.sh orchestrate --universe u001 --steps pool_mapping'
        if kwargs.get('days'):
            command += f" --days {kwargs['days']}"
        if kwargs.get('coins'):
            command += f" --coins {kwargs['coins']}"
        return command

    if action == 'holders_catchup':
        command = './scripts/manage.sh orchestrate --universe u001 --steps holders'
        if kwargs.get('days'):
            command += f" --days {kwargs['days']}"
        if kwargs.get('coins'):
            command += f" --coins {kwargs['coins']}"
        if kwargs.get('mature_only'):
            command += " --mature-only"
        return command

    if action == 'rd001_recent':
        max_coins = kwargs.get('max_coins', 25)
        return f'./scripts/run_batch.sh --max-coins {max_coins} --max-new-sigs 1000'

    if action == 'truth_rd001_chain_audit':
        return './scripts/manage.sh audit_u001_rd001_chain'

    if action == 'truth_fl001_derived_audit':
        return './scripts/manage.sh audit_u001_fl001_derived'

    if action == 'rd001_partial_historical':
        max_coins = kwargs.get('max_coins', 5)
        return f'./scripts/run_batch_partials_historical.sh --max-coins {max_coins}'

    if action == 'rd001_error_recovery':
        max_coins = kwargs.get('max_coins', 10)
        return f'./scripts/run_batch_errors.sh --max-coins {max_coins} --max-new-sigs 1000'

    if action == 'rd001_guarded':
        max_coins = kwargs.get('max_coins', 1)
        return (
            'MARJON_U001_RD001_MAX_FILTERED_SIGNATURES=1400 '
            f'./scripts/run_batch_partials_guarded.sh --max-coins {max_coins}'
        )

    if action == 'no_action':
        return 'No manual command recommended. Review controller state and queue pages first.'

    return None
