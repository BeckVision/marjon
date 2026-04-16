from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from pipeline.u001_automation import collect_metrics, get_or_create_state, select_next_action
from warehouse.models import (
    U001AutomationTick,
    U001FL001DerivedAuditRun,
    MigratedCoin,
    PoolMapping,
    RawTransaction,
    U001AutomationState,
    U001PipelineStatus,
    U001RD001ChainAuditRun,
    U001SourceAuditRun,
)


class U001AutomationPolicyTest(TestCase):
    def test_select_next_action_refreshes_stale_discovery(self):
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_STALE',
            anchor_event=timezone.now() - timedelta(days=2),
        )
        stale_time = timezone.now() - timedelta(days=3)
        MigratedCoin.objects.filter(pk=coin.pk).update(ingested_at=stale_time)

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'refresh_core')

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0.5',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
        },
        clear=False,
    )
    def test_select_next_action_uses_recent_pool_mapping_lane_when_discovery_is_fresh(self):
        now = timezone.now()
        for index in range(4):
            coin = MigratedCoin.objects.create(
                mint_address=f'AUTO_POOL_RECENT_{index}',
                anchor_event=now - timedelta(hours=index + 1),
            )
            if index == 0:
                PoolMapping.objects.create(
                    coin=coin,
                    pool_address='POOL-AUTO-RECENT',
                    dex='pumpswap',
                    source='fixture',
                )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'pool_mapping_recent')
        self.assertEqual(decision.command, 'orchestrate')
        self.assertEqual(decision.kwargs['steps'], 'pool_mapping')

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS': '24',
        },
        clear=False,
    )
    def test_select_next_action_runs_source_audit_when_truth_coverage_is_missing(self):
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_TRUTH_SRC',
            anchor_event=timezone.now() - timedelta(hours=6),
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=timezone.now() - timedelta(hours=1),
            completed_at=timezone.now() - timedelta(minutes=55),
            status='ok',
            options={'rpc_source': 'fixture'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=timezone.now() - timedelta(hours=1),
            completed_at=timezone.now() - timedelta(minutes=55),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'truth_source_audit')
        self.assertEqual(decision.command, 'audit_u001_sources')

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS': '999',
        },
        clear=False,
    )
    def test_select_next_action_prefers_recent_rd001_when_safe_coin_exists(self):
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_RECENT',
            anchor_event=timezone.now() - timedelta(hours=6),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL_RECENT',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=timezone.now() - timedelta(minutes=5),
            tx_signature='sig-auto-recent',
            trade_type='BUY',
            wallet_address='wallet-auto',
            token_amount=10,
            sol_amount=1,
            pool_address='POOL_RECENT',
            tx_fee='0.001',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=timezone.now() - timedelta(minutes=1),
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'rd001_recent')

    @patch('pipeline.u001_automation._dedicated_recent_runner_active', return_value=True)
    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
        },
        clear=False,
    )
    def test_select_next_action_skips_recent_lane_when_dedicated_runner_is_healthy(self, _runner_active):
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_RECENT_RUNNER',
            anchor_event=timezone.now() - timedelta(hours=6),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL_RECENT_RUNNER',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=timezone.now() - timedelta(minutes=5),
            tx_signature='sig-auto-recent-runner',
            trade_type='BUY',
            wallet_address='wallet-auto-runner',
            token_amount=10,
            sol_amount=1,
            pool_address='POOL_RECENT_RUNNER',
            tx_fee='0.001',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=timezone.now() - timedelta(minutes=1),
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'truth_rd001_chain_audit')

    @patch('pipeline.u001_automation._dedicated_recent_runner_active', return_value=True)
    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '1',
        },
        clear=False,
    )
    def test_select_next_action_prefers_historical_partial_when_recent_runner_is_healthy(self, _runner_active):
        now = timezone.now()
        historical_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_HIST_RUNNER',
            anchor_event=now - timedelta(days=5),
        )
        PoolMapping.objects.create(
            coin=historical_coin,
            pool_address='POOL-HIST-RUNNER',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=historical_coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(hours=2),
        )
        MigratedCoin.objects.create(
            mint_address='AUTO_RD001_MATURE_NO_HOLDERS',
            anchor_event=now - timedelta(days=7),
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics(now=now))

        self.assertEqual(decision.action, 'rd001_partial_historical')
        self.assertEqual(decision.command, 'fetch_transactions_batch')

    @patch('pipeline.u001_automation._dedicated_recent_runner_active', return_value=True)
    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '1',
            'MARJON_U001_AUTOMATION_ERROR_EVERY_N_TICKS': '1',
        },
        clear=False,
    )
    def test_select_next_action_prefers_error_recovery_when_recent_runner_is_healthy(self, _runner_active):
        now = timezone.now()
        historical_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_ERR_RUNNER',
            anchor_event=now - timedelta(days=5),
        )
        PoolMapping.objects.create(
            coin=historical_coin,
            pool_address='POOL-ERR-RUNNER',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=historical_coin,
            layer_id='RD-001',
            status='error',
            last_run_at=now - timedelta(hours=3),
        )
        MigratedCoin.objects.create(
            mint_address='AUTO_RD001_MATURE_NO_HOLDERS_ERR',
            anchor_event=now - timedelta(days=7),
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics(now=now))

        self.assertEqual(decision.action, 'rd001_error_recovery')
        self.assertEqual(decision.command, 'fetch_transactions_batch')
        self.assertEqual(decision.kwargs['source'], 'helius')

    @patch('pipeline.u001_automation._dedicated_recent_runner_active', return_value=True)
    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '1',
            'MARJON_U001_AUTOMATION_ERROR_EVERY_N_TICKS': '1',
        },
        clear=False,
    )
    def test_select_next_action_prefers_error_recovery_over_partial_when_due_and_recent_runner_is_healthy(self, _runner_active):
        now = timezone.now()
        partial_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_PARTIAL_AND_ERROR',
            anchor_event=now - timedelta(days=5),
        )
        error_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_ERROR_AND_PARTIAL',
            anchor_event=now - timedelta(days=6),
        )
        for coin, pool in (
            (partial_coin, 'POOL-PARTIAL-AND-ERROR'),
            (error_coin, 'POOL-ERROR-AND-PARTIAL'),
        ):
            PoolMapping.objects.create(
                coin=coin,
                pool_address=pool,
                dex='pumpswap',
                source='fixture',
            )
        U001PipelineStatus.objects.create(
            coin=partial_coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(hours=2),
        )
        U001PipelineStatus.objects.create(
            coin=error_coin,
            layer_id='RD-001',
            status='error',
            last_run_at=now - timedelta(hours=3),
        )
        MigratedCoin.objects.create(
            mint_address='AUTO_RD001_MATURE_NO_HOLDERS_ERR_DUE',
            anchor_event=now - timedelta(days=7),
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics(now=now))

        self.assertEqual(decision.action, 'rd001_error_recovery')
        self.assertEqual(decision.kwargs['source'], 'helius')

    @patch('pipeline.u001_automation._dedicated_recent_runner_active', return_value=True)
    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '1',
        },
        clear=False,
    )
    def test_select_next_action_does_not_run_guarded_while_regular_rd001_backlog_exists(self, _runner_active):
        now = timezone.now()
        partial_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_PARTIAL_BLOCKS_GUARDED',
            anchor_event=now - timedelta(days=5),
        )
        guarded_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_GUARDED_BLOCKED',
            anchor_event=now - timedelta(days=6),
        )
        for coin, pool in (
            (partial_coin, 'POOL-PARTIAL-BLOCKS-GUARDED'),
            (guarded_coin, 'POOL-GUARDED-BLOCKED'),
        ):
            PoolMapping.objects.create(
                coin=coin,
                pool_address=pool,
                dex='pumpswap',
                source='fixture',
            )
        U001PipelineStatus.objects.create(
            coin=partial_coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(hours=2),
        )
        U001PipelineStatus.objects.create(
            coin=guarded_coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(hours=3),
            last_error='Filtered signature count 1200 exceeds free-tier guard (1000)',
        )
        MigratedCoin.objects.create(
            mint_address='AUTO_RD001_MATURE_NO_HOLDERS_GUARD',
            anchor_event=now - timedelta(days=7),
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics(now=now))

        self.assertEqual(decision.action, 'rd001_partial_historical')

    @patch('pipeline.u001_automation._dedicated_recent_runner_active', return_value=True)
    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '1',
            'MARJON_U001_RD001_GUARDED_MAX_FILTERED_SIGNATURES': '1400',
        },
        clear=False,
    )
    def test_select_next_action_guarded_lane_includes_raised_filtered_signature_limit(self, _runner_active):
        now = timezone.now()
        guarded_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_GUARDED_ONLY',
            anchor_event=now - timedelta(days=6),
        )
        PoolMapping.objects.create(
            coin=guarded_coin,
            pool_address='POOL-GUARDED-ONLY',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=guarded_coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(hours=3),
            last_error='Filtered signature count 1200 exceeds free-tier guard (1000)',
        )
        MigratedCoin.objects.create(
            mint_address='AUTO_RD001_MATURE_NO_HOLDERS_GUARDED_ONLY',
            anchor_event=now - timedelta(days=7),
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics(now=now))

        self.assertEqual(decision.action, 'rd001_guarded')
        self.assertEqual(decision.kwargs['source'], 'helius')
        self.assertEqual(decision.kwargs['max_filtered_signatures'], 1400)

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '1',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
        },
        clear=False,
    )
    def test_select_next_action_prefers_recent_rd001_bootstrap_before_holders(self):
        recent_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_BOOTSTRAP',
            anchor_event=timezone.now() - timedelta(hours=6),
        )
        old_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_BOOTSTRAP_OLD',
            anchor_event=timezone.now() - timedelta(days=5),
        )
        PoolMapping.objects.create(
            coin=recent_coin,
            pool_address='POOL-RD001-BOOTSTRAP',
            dex='pumpswap',
            source='fixture',
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'rd001_recent')
        self.assertIn('bootstrap', decision.reason.lower())

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS': '999',
            'MARJON_U001_AUTOMATION_TRUTH_RD001_CHAIN_AUDIT_COOLDOWN_HOURS': '24',
        },
        clear=False,
    )
    def test_select_next_action_runs_rd001_chain_audit_after_recent_lane_is_clear(self):
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_TRUTH_CHAIN',
            anchor_event=timezone.now() - timedelta(days=5),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL-TRUTH-CHAIN',
            dex='pumpswap',
            source='fixture',
        )
        U001SourceAuditRun.objects.create(
            started_at=timezone.now() - timedelta(hours=1),
            completed_at=timezone.now() - timedelta(minutes=55),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'truth_rd001_chain_audit')
        self.assertEqual(decision.command, 'audit_u001_rd001_chain')

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS': '999',
            'MARJON_U001_AUTOMATION_TRUTH_RD001_CHAIN_AUDIT_COOLDOWN_HOURS': '999',
            'MARJON_U001_AUTOMATION_TRUTH_FL001_DERIVED_AUDIT_COOLDOWN_HOURS': '24',
        },
        clear=False,
    )
    def test_select_next_action_runs_fl001_derived_audit_when_it_is_stale(self):
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_TRUTH_DERIVED',
            anchor_event=timezone.now() - timedelta(days=5),
        )
        U001SourceAuditRun.objects.create(
            started_at=timezone.now() - timedelta(hours=1),
            completed_at=timezone.now() - timedelta(minutes=55),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=timezone.now() - timedelta(hours=1),
            completed_at=timezone.now() - timedelta(minutes=55),
            status='ok',
            options={'rpc_source': 'fixture'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'truth_fl001_derived_audit')
        self.assertEqual(decision.command, 'audit_u001_fl001_derived')

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0.5',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '1',
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS': '999',
            'MARJON_U001_AUTOMATION_HOLDERS_FORCE_AFTER_RECENT_TICKS': '2',
        },
        clear=False,
    )
    def test_select_next_action_forces_holders_after_recent_focused_streak(self):
        now = timezone.now()
        old_coin = MigratedCoin.objects.create(
            mint_address='AUTO_HOLDERS_STREAK_OLD',
            anchor_event=now - timedelta(days=5),
        )
        for index in range(4):
            coin = MigratedCoin.objects.create(
                mint_address=f'AUTO_HOLDERS_STREAK_RECENT_{index}',
                anchor_event=now - timedelta(hours=index + 1),
            )
            if index == 0:
                PoolMapping.objects.create(
                    coin=coin,
                    pool_address='POOL-HOLDERS-STREAK',
                    dex='pumpswap',
                    source='fixture',
                )

        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=20),
            completed_at=now - timedelta(minutes=19),
            action='pool_mapping_recent',
            reason='fixture',
            status='complete',
            command='orchestrate',
            command_kwargs={},
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=9),
            action='rd001_recent',
            reason='fixture',
            status='complete',
            command='fetch_transactions_batch',
            command_kwargs={},
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics(now=now))

        self.assertEqual(decision.action, 'holders_catchup')
        self.assertEqual(decision.command, 'orchestrate')
        self.assertEqual(decision.kwargs['steps'], 'holders')
        self.assertIn('recent-focused', decision.reason.lower())

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '1',
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS': '999',
            'MARJON_U001_AUTOMATION_TRUTH_RD001_CHAIN_AUDIT_COOLDOWN_HOURS': '999',
            'MARJON_U001_AUTOMATION_TRUTH_FL001_DERIVED_AUDIT_COOLDOWN_HOURS': '999',
            'MARJON_U001_AUTOMATION_HOLDERS_NO_PROGRESS_AFTER_TICKS': '4',
            'MARJON_U001_AUTOMATION_HOLDERS_NO_PROGRESS_PAUSE_HOURS': '6',
        },
        clear=False,
    )
    def test_select_next_action_pauses_holders_after_repeated_zero_load_ticks(self):
        now = timezone.now()
        MigratedCoin.objects.create(
            mint_address='AUTO_HOLDERS_ZERO_LOAD_OLD',
            anchor_event=now - timedelta(days=5),
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(minutes=55),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(minutes=55),
            status='ok',
            options={'rpc_source': 'fixture'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(minutes=55),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        for index in range(4):
            U001AutomationTick.objects.create(
                started_at=now - timedelta(minutes=(index + 1) * 10),
                completed_at=now - timedelta(minutes=(index + 1) * 10 - 1),
                action='holders_catchup',
                reason='fixture',
                status='complete',
                command='orchestrate',
                command_kwargs={},
                result_summary={
                    'steps': {
                        'holders': {
                            'records_loaded': 0,
                        },
                    },
                },
            )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics(now=now))

        self.assertEqual(decision.action, 'no_action')

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS': '999',
            'MARJON_U001_AUTOMATION_RD001_TRANSPORT_PAUSE_HOURS': '4',
        },
        clear=False,
    )
    def test_select_next_action_skips_recent_lane_during_transport_pause(self):
        recent_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_PAUSE',
            anchor_event=timezone.now() - timedelta(hours=6),
        )
        historical_coin = MigratedCoin.objects.create(
            mint_address='AUTO_RD001_HIST',
            anchor_event=timezone.now() - timedelta(days=5),
        )
        for coin, pool in (
            (recent_coin, 'POOL-RECENT-PAUSE'),
            (historical_coin, 'POOL-HIST-PAUSE'),
        ):
            PoolMapping.objects.create(
                coin=coin,
                pool_address=pool,
                dex='pumpswap',
                source='fixture',
            )

        RawTransaction.objects.create(
            coin=recent_coin,
            timestamp=timezone.now() - timedelta(minutes=5),
            tx_signature='sig-auto-pause',
            trade_type='BUY',
            wallet_address='wallet-auto-pause',
            token_amount=10,
            sol_amount=1,
            pool_address='POOL-RECENT-PAUSE',
            tx_fee='0.001',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=recent_coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=timezone.now() - timedelta(minutes=1),
        )
        U001PipelineStatus.objects.create(
            coin=historical_coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=timezone.now() - timedelta(hours=2),
        )
        U001SourceAuditRun.objects.create(
            started_at=timezone.now() - timedelta(hours=1),
            completed_at=timezone.now() - timedelta(minutes=55),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=timezone.now() - timedelta(hours=1),
            completed_at=timezone.now() - timedelta(minutes=55),
            status='ok',
            options={'rpc_source': 'fixture'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=timezone.now() - timedelta(hours=1),
            completed_at=timezone.now() - timedelta(minutes=55),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        state = get_or_create_state()
        state.last_action = 'rd001_recent'
        state.last_action_status = 'error'
        state.last_action_completed_at = timezone.now() - timedelta(minutes=30)
        state.notes = 'transport_error: Server disconnected without sending a response.'
        state.save()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'rd001_partial_historical')

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '1',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS': '999',
            'MARJON_U001_AUTOMATION_HOLDERS_AUTH_PAUSE_HOURS': '8',
        },
        clear=False,
    )
    def test_select_next_action_skips_holders_lane_during_auth_pause(self):
        old_coin = MigratedCoin.objects.create(
            mint_address='AUTO_HOLDERS_AUTH',
            anchor_event=timezone.now() - timedelta(days=5),
        )
        recent_coin = MigratedCoin.objects.create(
            mint_address='AUTO_HOLDERS_RD001',
            anchor_event=timezone.now() - timedelta(hours=6),
        )
        PoolMapping.objects.create(
            coin=recent_coin,
            pool_address='POOL-HOLDERS-RD001',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=recent_coin,
            timestamp=timezone.now() - timedelta(minutes=5),
            tx_signature='sig-holders-rd001',
            trade_type='BUY',
            wallet_address='wallet-holders-rd001',
            token_amount=10,
            sol_amount=1,
            pool_address='POOL-HOLDERS-RD001',
            tx_fee='0.001',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=recent_coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=timezone.now() - timedelta(minutes=1),
        )

        state = get_or_create_state()
        state.last_action = 'holders_catchup'
        state.last_action_status = 'error'
        state.last_action_completed_at = timezone.now() - timedelta(minutes=20)
        state.notes = '401 Unauthorized from Moralis'
        state.save()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'rd001_recent')

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_MAX_CONSECUTIVE_FAILURES': '3',
            'MARJON_U001_AUTOMATION_FAILURE_PAUSE_HOURS': '4',
        },
        clear=False,
    )
    def test_select_next_action_enters_failure_pause_after_repeated_errors(self):
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_FAIL_PAUSE',
            anchor_event=timezone.now() - timedelta(hours=6),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL-FAIL-PAUSE',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=timezone.now() - timedelta(minutes=5),
            tx_signature='sig-fail-pause',
            trade_type='BUY',
            wallet_address='wallet-fail-pause',
            token_amount=10,
            sol_amount=1,
            pool_address='POOL-FAIL-PAUSE',
            tx_fee='0.001',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status='partial',
        )

        state = get_or_create_state()
        state.last_action = 'rd001_recent'
        state.last_action_status = 'error'
        state.last_action_completed_at = timezone.now() - timedelta(minutes=15)
        state.consecutive_failures = 3
        state.notes = 'transport_error: network error'
        state.save()

        decision = select_next_action(state, collect_metrics())

        self.assertEqual(decision.action, 'no_action')
        self.assertIn('cooldown', decision.reason.lower())

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_MAX_CONSECUTIVE_FAILURES': '99',
            'MARJON_U001_AUTOMATION_CONNECTIVITY_PAUSE_AFTER_ERRORS': '2',
            'MARJON_U001_AUTOMATION_CONNECTIVITY_PAUSE_HOURS': '2',
        },
        clear=False,
    )
    def test_select_next_action_enters_connectivity_pause_before_generic_failure_pause(self):
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_CONNECTIVITY_PAUSE',
            anchor_event=timezone.now() - timedelta(hours=6),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL-CONNECTIVITY-PAUSE',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=timezone.now() - timedelta(minutes=5),
            tx_signature='sig-connectivity-pause',
            trade_type='BUY',
            wallet_address='wallet-connectivity-pause',
            token_amount=10,
            sol_amount=1,
            pool_address='POOL-CONNECTIVITY-PAUSE',
            tx_fee='0.001',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status='partial',
        )
        now = timezone.now()
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=20),
            completed_at=now - timedelta(minutes=19),
            action='rd001_recent',
            reason='fixture',
            status='error',
            command='fetch_transactions_batch',
            command_kwargs={},
            notes='transport_error: network error',
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=9),
            action='truth_source_audit',
            reason='fixture',
            status='error',
            command='audit_u001_sources',
            command_kwargs={},
            notes='transport_error: Temporary failure in name resolution',
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics(now=now))

        self.assertEqual(decision.action, 'no_action')
        self.assertIn('reachability', decision.reason.lower())

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_MAX_CONSECUTIVE_FAILURES': '99',
            'MARJON_U001_AUTOMATION_CONNECTIVITY_PAUSE_AFTER_ERRORS': '2',
            'MARJON_U001_AUTOMATION_CONNECTIVITY_PAUSE_HOURS': '1',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS': '999',
        },
        clear=False,
    )
    def test_select_next_action_allows_work_after_connectivity_pause_window_expires(self):
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_CONNECTIVITY_EXPIRE',
            anchor_event=timezone.now() - timedelta(hours=6),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL-CONNECTIVITY-EXPIRE',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=timezone.now() - timedelta(minutes=5),
            tx_signature='sig-connectivity-expire',
            trade_type='BUY',
            wallet_address='wallet-connectivity-expire',
            token_amount=10,
            sol_amount=1,
            pool_address='POOL-CONNECTIVITY-EXPIRE',
            tx_fee='0.001',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=timezone.now() - timedelta(minutes=1),
        )
        now = timezone.now()
        U001AutomationTick.objects.create(
            started_at=now - timedelta(hours=3),
            completed_at=now - timedelta(hours=3) + timedelta(minutes=1),
            action='rd001_recent',
            reason='fixture',
            status='error',
            command='fetch_transactions_batch',
            command_kwargs={},
            notes='transport_error: network error',
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=2) + timedelta(minutes=1),
            action='truth_source_audit',
            reason='fixture',
            status='error',
            command='audit_u001_sources',
            command_kwargs={},
            notes='transport_error: Temporary failure in name resolution',
        )

        state = get_or_create_state()

        decision = select_next_action(state, collect_metrics(now=now))

        self.assertEqual(decision.action, 'rd001_recent')


class AutomateU001CommandTest(TestCase):
    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0.5',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
        },
        clear=False,
    )
    def test_command_runs_recent_pool_mapping_lane(self):
        now = timezone.now()
        for index in range(4):
            coin = MigratedCoin.objects.create(
                mint_address=f'AUTO_CMD_POOL_{index}',
                anchor_event=now - timedelta(hours=index + 1),
            )
            if index == 0:
                PoolMapping.objects.create(
                    coin=coin,
                    pool_address='POOL-CMD-POOL',
                    dex='pumpswap',
                    source='fixture',
                )

        with patch('pipeline.management.commands.automate_u001.call_command') as inner_call:
            call_command('automate_u001')

        state = U001AutomationState.objects.get(singleton_key='u001')
        tick = U001AutomationTick.objects.get()
        self.assertEqual(state.last_action, 'pool_mapping_recent')
        self.assertEqual(state.last_action_status, 'complete')
        self.assertEqual(tick.action, 'pool_mapping_recent')
        self.assertEqual(
            [item.args[0] for item in inner_call.call_args_list],
            ['repair_u001_ingestion', 'orchestrate', 'snapshot_u001_ops'],
        )
        self.assertEqual(inner_call.call_args_list[1].kwargs['steps'], 'pool_mapping')
        self.assertEqual(tick.result_summary, {})

    def test_command_runs_truth_source_audit_lane(self):
        MigratedCoin.objects.create(
            mint_address='AUTO_CMD_TRUTH_SOURCE',
            anchor_event=timezone.now() - timedelta(hours=6),
        )

        with patch('pipeline.management.commands.automate_u001.call_command') as inner_call:
            call_command('automate_u001', force_action='truth_source_audit', skip_snapshot=True)

        state = U001AutomationState.objects.get(singleton_key='u001')
        tick = U001AutomationTick.objects.get()
        self.assertEqual(state.last_action, 'truth_source_audit')
        self.assertEqual(tick.action, 'truth_source_audit')
        self.assertEqual(
            [item.args[0] for item in inner_call.call_args_list],
            ['repair_u001_ingestion', 'audit_u001_sources'],
        )

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS': '999',
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
            'MARJON_U001_AUTOMATION_TRUTH_SOURCE_AUDIT_COOLDOWN_HOURS': '999',
        },
        clear=False,
    )
    def test_command_persists_structured_result_summary_for_rd001_lane(self):
        now = timezone.now()
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_CMD_RD001_RECENT',
            anchor_event=now - timedelta(hours=6),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL-CMD-RD001',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=now - timedelta(minutes=5),
            tx_signature='sig-auto-cmd-rd001',
            trade_type='BUY',
            wallet_address='wallet-auto-cmd-rd001',
            token_amount=10,
            sol_amount=1,
            pool_address='POOL-CMD-RD001',
            tx_fee='0.001',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(minutes=1),
        )

        def _fake_call(name, *args, **kwargs):
            if name == 'fetch_transactions_batch':
                return {
                    'source': 'auto',
                    'status_filter': 'incomplete',
                    'dry_run': False,
                    'active_coins': 1,
                    'queued_coins': 1,
                    'succeeded': 1,
                    'failed': 0,
                    'records_loaded': 42,
                    'records_skipped': 1,
                    'api_calls': 3,
                }
            return None

        with patch('pipeline.management.commands.automate_u001.call_command', side_effect=_fake_call):
            call_command('automate_u001', skip_snapshot=True)

        tick = U001AutomationTick.objects.get()
        self.assertEqual(tick.action, 'rd001_recent')
        self.assertEqual(tick.result_summary['records_loaded'], 42)
        self.assertEqual(tick.result_summary['api_calls'], 3)

    def test_command_marks_batch_lane_error_when_all_queued_coins_fail(self):
        def _fake_call(name, *args, **kwargs):
            if name == 'fetch_transactions_batch':
                return {
                    'source': 'helius',
                    'status_filter': 'partial',
                    'dry_run': False,
                    'active_coins': 5,
                    'queued_coins': 5,
                    'succeeded': 0,
                    'failed': 5,
                    'records_loaded': 0,
                    'records_skipped': 0,
                    'api_calls': 0,
                }
            return None

        with (
            patch('pipeline.management.commands.automate_u001.call_command', side_effect=_fake_call),
            self.assertRaisesMessage(
                CommandError,
                'rd001_partial_historical queued 5 coin(s) but all failed with 0 rows loaded',
            ),
        ):
            call_command(
                'automate_u001',
                force_action='rd001_partial_historical',
                skip_snapshot=True,
            )

        state = U001AutomationState.objects.get(singleton_key='u001')
        tick = U001AutomationTick.objects.get()
        self.assertEqual(state.last_action, 'rd001_partial_historical')
        self.assertEqual(state.last_action_status, 'error')
        self.assertEqual(tick.action, 'rd001_partial_historical')
        self.assertEqual(tick.status, 'error')
        self.assertIn('all failed with 0 rows loaded', tick.notes)

    def test_command_persists_structured_result_summary_for_orchestrate_lane(self):
        def _fake_call(name, *args, **kwargs):
            if name == 'orchestrate':
                return {
                    'universe': 'U-001',
                    'dry_run': False,
                    'loops': 1,
                    'total_succeeded': 4,
                    'total_failed': 0,
                    'total_skipped': 6,
                    'discovery': None,
                    'steps': {
                        'holders': {
                            'mode': 'per_coin',
                            'succeeded': 4,
                            'failed': 0,
                            'skipped': 6,
                            'records_loaded': 4004,
                        },
                    },
                }
            return None

        with patch('pipeline.management.commands.automate_u001.call_command', side_effect=_fake_call):
            call_command(
                'automate_u001',
                force_action='holders_catchup',
                skip_snapshot=True,
            )

        tick = U001AutomationTick.objects.get()
        self.assertEqual(tick.action, 'holders_catchup')
        self.assertEqual(tick.result_summary['total_succeeded'], 4)
        self.assertEqual(
            tick.result_summary['steps']['holders']['records_loaded'],
            4004,
        )

    def test_command_runs_truth_rd001_chain_audit_lane(self):
        MigratedCoin.objects.create(
            mint_address='AUTO_CMD_TRUTH_CHAIN',
            anchor_event=timezone.now() - timedelta(hours=6),
        )

        with patch('pipeline.management.commands.automate_u001.call_command') as inner_call:
            call_command('automate_u001', force_action='truth_rd001_chain_audit', skip_snapshot=True)

        tick = U001AutomationTick.objects.get()
        self.assertEqual(tick.action, 'truth_rd001_chain_audit')
        self.assertEqual(
            [item.args[0] for item in inner_call.call_args_list],
            ['repair_u001_ingestion', 'audit_u001_rd001_chain'],
        )

    def test_command_runs_truth_fl001_derived_audit_lane(self):
        MigratedCoin.objects.create(
            mint_address='AUTO_CMD_TRUTH_DERIVED',
            anchor_event=timezone.now() - timedelta(hours=6),
        )

        with patch('pipeline.management.commands.automate_u001.call_command') as inner_call:
            call_command('automate_u001', force_action='truth_fl001_derived_audit', skip_snapshot=True)

        tick = U001AutomationTick.objects.get()
        self.assertEqual(tick.action, 'truth_fl001_derived_audit')
        self.assertEqual(
            [item.args[0] for item in inner_call.call_args_list],
            ['repair_u001_ingestion', 'audit_u001_fl001_derived'],
        )

    @patch.dict(
        'os.environ',
        {
            'MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT': '0',
            'MARJON_U001_AUTOMATION_RECENT_MAPPING_MIN_PCT': '0',
        },
        clear=False,
    )
    def test_command_updates_state_and_runs_selected_action(self):
        coin = MigratedCoin.objects.create(
            mint_address='AUTO_CMD_RECENT',
            anchor_event=timezone.now() - timedelta(hours=6),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL_CMD_RECENT',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=timezone.now() - timedelta(minutes=5),
            tx_signature='sig-auto-cmd',
            trade_type='BUY',
            wallet_address='wallet-auto-cmd',
            token_amount=10,
            sol_amount=1,
            pool_address='POOL_CMD_RECENT',
            tx_fee='0.001',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status='partial',
        )

        with patch('pipeline.management.commands.automate_u001.call_command') as inner_call:
            call_command('automate_u001')

        state = U001AutomationState.objects.get(singleton_key='u001')
        tick = U001AutomationTick.objects.get()
        self.assertEqual(state.last_action, 'rd001_recent')
        self.assertEqual(state.last_action_status, 'complete')
        self.assertEqual(state.error_lane_tick_counter, 1)
        self.assertEqual(state.last_snapshot_date, timezone.localdate())
        self.assertEqual(tick.action, 'rd001_recent')
        self.assertEqual(tick.status, 'complete')
        self.assertTrue(tick.repaired_state)
        self.assertTrue(tick.snapshot_taken)
        self.assertEqual(
            [item.args[0] for item in inner_call.call_args_list],
            ['repair_u001_ingestion', 'fetch_transactions_batch', 'snapshot_u001_ops'],
        )
        self.assertEqual(
            inner_call.call_args_list[1].kwargs['max_coins'],
            25,
        )

    def test_command_dry_run_does_not_create_state_side_effects(self):
        call_command('automate_u001', dry_run=True)

        state = U001AutomationState.objects.get(singleton_key='u001')
        self.assertIsNone(state.last_tick_at)
        self.assertIsNone(state.last_action)
        self.assertEqual(U001AutomationTick.objects.count(), 0)
