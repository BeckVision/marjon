import os
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pipeline.management.commands.fetch_transactions import SHYFT_RETENTION_DAYS
from warehouse.models import (
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
    U001FL001DerivedAuditRun,
    U001OpsSnapshot,
    U001PipelineRun,
    U001PipelineStatus,
    U001RD001ChainAuditRun,
    U001SourceAuditRun,
)


class VisualizationViewsTest(TestCase):
    def test_home_page_renders(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Not just candles. Actual trade flow.')
        self.assertContains(response, 'Understand RD-001')

    def test_chart_view_still_renders(self):
        response = self.client.get(reverse('chart', args=['BTCUSDT']))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'BTCUSDT')

    def test_u001_ops_overview_renders_summary(self):
        self._seed_u001_overview_fixture()

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / 'runner_status.txt'
            self._write_runner_status(status_path, pid=os.getpid(), state='running', cycle='5')
            with self.settings(U001_RD001_RECENT_RUNNER_STATUS_FILE=str(status_path)):
                response = self.client.get(reverse('u001_ops_overview'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'U-001 Operations Cockpit')
        self.assertContains(response, 'Automation Controller')
        self.assertContains(response, 'Boot Recovery')
        self.assertContains(response, 'Latest reboot recovery completed')
        self.assertContains(response, 'RD-001 Recent Runner')
        self.assertContains(response, 'Dedicated recent RD-001 runner is active')
        self.assertContains(response, 'Connectivity Risk')
        self.assertContains(response, 'No active connectivity signal')
        self.assertContains(response, 'Spin Risk')
        self.assertContains(response, 'No active spin risk')
        self.assertContains(response, 'Truth Audit Coverage')
        self.assertContains(response, 'Recent truth coverage has gaps')
        self.assertContains(response, 'Live Source Audit')
        self.assertContains(response, 'RD-001 Chain Audit')
        self.assertContains(response, 'Latest RD-001 chain audit was partial')
        self.assertContains(response, 'FL-001 Derived Audit')
        self.assertContains(response, 'Latest FL-001 derived audit passed')
        self.assertContains(response, 'Recent Automation Ticks')
        self.assertContains(response, 'rd001_recent')
        self.assertContains(response, 'Recent pool mapping needs dedicated catch-up')
        self.assertContains(response, 'Recent U-001 Batches')
        self.assertContains(response, 'Last 6 discovered coins')

    def test_u001_ops_summary_api_returns_expected_counts(self):
        self._seed_u001_overview_fixture()

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / 'runner_status.txt'
            self._write_runner_status(status_path, pid=os.getpid(), state='sleeping', cycle='4')
            with self.settings(U001_RD001_RECENT_RUNNER_STATUS_FILE=str(status_path)):
                response = self.client.get(reverse('u001_ops_summary_api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['headline']['total_coins'], 6)
        self.assertEqual(payload['automation']['last_action'], 'rd001_recent')
        self.assertEqual(payload['automation']['last_action_status'], 'complete')
        self.assertEqual(len(payload['automation']['recent_ticks']), 1)
        self.assertEqual(payload['automation']['recent_ticks'][0]['action'], 'rd001_recent')
        self.assertEqual(payload['automation']['recent_ticks'][0]['result_summary']['records_loaded'], 321)
        self.assertEqual(payload['boot_recovery']['status'], 'complete')
        self.assertEqual(payload['boot_recovery']['automation_tick_status'], 'complete')
        self.assertEqual(payload['rd001_recent_runner']['state'], 'healthy')
        self.assertEqual(payload['rd001_recent_runner']['status'], 'sleeping')
        self.assertTrue(payload['rd001_recent_runner']['pid_alive'])
        self.assertEqual(payload['rd001_recent_runner']['cycle'], 4)
        self.assertEqual(payload['connectivity_risk']['state'], 'healthy')
        self.assertEqual(payload['connectivity_risk']['streak_length'], 0)
        self.assertEqual(payload['spin_risk']['state'], 'healthy')
        self.assertEqual(payload['spin_risk']['action'], 'rd001_recent')
        self.assertEqual(payload['truth_audit_coverage']['state'], 'warn')
        self.assertEqual(payload['truth_audit_coverage']['days'], 7)
        self.assertEqual(payload['truth_audit_coverage']['days_with_any'], 1)
        self.assertEqual(payload['truth_audit_coverage']['days_without_any'], 6)
        self.assertEqual(payload['truth_audit_coverage']['days_with_full'], 1)
        self.assertEqual(payload['truth_audit_coverage']['days_with_findings'], 0)
        self.assertEqual(payload['truth_audit_coverage']['days_with_warnings'], 1)
        self.assertEqual(payload['source_audit']['status'], 'warning')
        self.assertEqual(payload['source_audit']['finding_count'], 0)
        self.assertEqual(payload['source_audit']['warning_count'], 1)
        self.assertEqual(payload['rd001_chain_audit']['status'], 'warning')
        self.assertEqual(payload['rd001_chain_audit']['warning_count'], 1)
        self.assertEqual(payload['rd001_chain_audit']['coin_count'], 1)
        self.assertEqual(payload['rd001_chain_audit']['rpc_source'], 'public_fallback')
        self.assertEqual(payload['fl001_derived_audit']['status'], 'ok')
        self.assertEqual(payload['fl001_derived_audit']['candle_count'], 3)
        self.assertEqual(payload['recommendation']['source'], 'controller')
        self.assertEqual(payload['headline']['mapped_coins'], 1)
        self.assertEqual(payload['recent_coverage']['discovered_count'], 6)
        self.assertEqual(payload['recent_coverage']['mapped_count'], 1)
        self.assertEqual(payload['recommendation']['title'], 'Recent pool mapping needs dedicated catch-up')
        self.assertEqual(
            payload['error_panels'][0]['buckets']['auth'],
            1,
        )

    def test_u001_ops_summary_api_surfaces_spin_risk_warning(self):
        self._seed_u001_automation_spin_fixture()

        response = self.client.get(reverse('u001_ops_summary_api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['spin_risk']['state'], 'critical')
        self.assertEqual(payload['spin_risk']['action'], 'pool_mapping_recent')
        self.assertIn('spinning', payload['spin_risk']['headline'].lower())
        self.assertIn('mapped nothing', payload['spin_risk']['detail'].lower())

    def test_u001_ops_summary_api_surfaces_connectivity_risk(self):
        self._seed_u001_connectivity_fixture()

        response = self.client.get(reverse('u001_ops_summary_api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['connectivity_risk']['state'], 'critical')
        self.assertEqual(payload['connectivity_risk']['streak_length'], 2)
        self.assertEqual(payload['connectivity_risk']['latest_action'], 'rd001_recent')
        self.assertIn('reachability', payload['connectivity_risk']['headline'].lower())
        self.assertIn('transport_error', payload['connectivity_risk']['latest_note'])

    def test_u001_ops_summary_api_surfaces_connectivity_recovery(self):
        self._seed_u001_connectivity_fixture()
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / 'runner_status.txt'
            self._write_runner_status(
                status_path,
                state='sleeping',
                updated_at=(timezone.now() - timedelta(minutes=1)).isoformat(),
                last_cycle_completed_at=(timezone.now() - timedelta(minutes=1)).isoformat(),
                last_exit_code='0',
            )
            with self.settings(U001_RD001_RECENT_RUNNER_STATUS_FILE=str(status_path)):
                response = self.client.get(reverse('u001_ops_summary_api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['connectivity_risk']['state'], 'healthy')
        self.assertTrue(payload['connectivity_risk']['recovered'])
        self.assertIn('recovered', payload['connectivity_risk']['headline'].lower())
        self.assertIsNotNone(payload['connectivity_risk']['recovered_at'])

    def test_u001_ops_summary_api_surfaces_policy_pause_recommendation(self):
        self._seed_u001_policy_pause_fixture()

        response = self.client.get(reverse('u001_ops_summary_api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['recommendation']['source'], 'controller')
        self.assertEqual(payload['recommendation']['title'], 'Automation is paused by policy')
        self.assertIn('cooldown', payload['recommendation']['detail'].lower())

    def test_u001_ops_summary_api_surfaces_recent_pool_mapping_recommendation(self):
        self._seed_u001_recent_pool_mapping_fixture()

        response = self.client.get(reverse('u001_ops_summary_api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['recommendation']['source'], 'controller')
        self.assertEqual(payload['recommendation']['title'], 'Recent pool mapping needs dedicated catch-up')

    def test_u001_ops_automation_view_renders_tick_history(self):
        self._seed_u001_overview_fixture()

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / 'runner_status.txt'
            self._write_runner_status(status_path, pid=os.getpid(), state='sleeping', cycle='4')
            with self.settings(U001_RD001_RECENT_RUNNER_STATUS_FILE=str(status_path)):
                response = self.client.get(reverse('u001_ops_automation'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'U-001 Automation')
        self.assertContains(response, 'Recent Ticks')
        self.assertContains(response, 'Current Manual Equivalent')
        self.assertContains(response, 'Connectivity Risk')
        self.assertContains(response, 'No active connectivity signal')
        self.assertContains(response, 'RD-001 Recent Runner')
        self.assertContains(response, 'sleeping between cycles')
        self.assertContains(response, 'Spin Risk')
        self.assertContains(response, 'No active spin risk')
        self.assertContains(response, 'Truth Audit Lanes')
        self.assertContains(response, 'Live Source Audit Lane')
        self.assertContains(response, './scripts/manage.sh orchestrate')
        self.assertContains(response, 'rd001_recent')
        self.assertContains(response, '321 loaded')

    def test_u001_ops_automation_api_filters_ticks(self):
        self._seed_u001_overview_fixture()
        now = timezone.now()
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=30),
            completed_at=now - timedelta(minutes=29),
            action='refresh_core',
            reason='Discovery freshness is past the configured stale threshold.',
            status='error',
            command='orchestrate',
            command_kwargs={'steps': 'discovery,pool_mapping,ohlcv'},
            repaired_state=True,
            snapshot_taken=False,
            notes='upstream timeout',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / 'runner_status.txt'
            self._write_runner_status(status_path, pid=os.getpid(), state='running', cycle='7')
            with self.settings(U001_RD001_RECENT_RUNNER_STATUS_FILE=str(status_path)):
                response = self.client.get(reverse('u001_ops_automation_api'), {
                    'action': 'refresh_core',
                    'status': 'error',
                    'limit': 10,
                })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['filters']['action'], 'refresh_core')
        self.assertEqual(payload['filters']['status'], 'error')
        self.assertEqual(payload['headline']['filtered_ticks'], 1)
        self.assertEqual(payload['ticks'][0]['action'], 'refresh_core')
        self.assertEqual(payload['ticks'][0]['status'], 'error')
        self.assertIn('./scripts/manage.sh orchestrate', payload['ticks'][0]['manual_command'])
        self.assertIn('./scripts/manage.sh orchestrate', payload['current_manual_equivalent']['command'])
        self.assertEqual(payload['rd001_recent_runner']['state'], 'healthy')
        self.assertEqual(payload['rd001_recent_runner']['status'], 'running')
        self.assertEqual(payload['rd001_recent_runner']['cycle'], 7)

    def test_u001_ops_automation_api_includes_truth_audit_lane_rollups(self):
        self._seed_u001_overview_fixture()
        now = timezone.now()
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=25),
            completed_at=now - timedelta(minutes=24),
            action='truth_source_audit',
            reason='Recent provider-source truth coverage is stale or missing.',
            status='complete',
            command='audit_u001_sources',
            command_kwargs={},
            repaired_state=True,
            snapshot_taken=False,
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=20),
            completed_at=now - timedelta(minutes=19),
            action='truth_rd001_chain_audit',
            reason='Recent direct-RPC RD-001 chain-truth coverage is stale or missing.',
            status='error',
            command='audit_u001_rd001_chain',
            command_kwargs={},
            repaired_state=True,
            snapshot_taken=False,
            notes='rpc timeout',
        )

        response = self.client.get(reverse('u001_ops_automation_api'), {'limit': 10})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        lanes = {row['action']: row for row in payload['truth_audit_lanes']}
        self.assertEqual(lanes['truth_source_audit']['total_ticks'], 1)
        self.assertEqual(lanes['truth_source_audit']['complete_ticks'], 1)
        self.assertEqual(lanes['truth_source_audit']['audit']['status'], 'warning')
        self.assertEqual(lanes['truth_rd001_chain_audit']['total_ticks'], 1)
        self.assertEqual(lanes['truth_rd001_chain_audit']['error_ticks'], 1)
        self.assertEqual(lanes['truth_rd001_chain_audit']['audit']['status'], 'warning')
        self.assertEqual(lanes['truth_fl001_derived_audit']['total_ticks'], 0)
        self.assertEqual(lanes['truth_fl001_derived_audit']['audit']['status'], 'ok')

    def test_u001_ops_automation_api_surfaces_connectivity_risk(self):
        self._seed_u001_connectivity_fixture()

        response = self.client.get(reverse('u001_ops_automation_api'), {'limit': 10})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['connectivity_risk']['state'], 'critical')
        self.assertEqual(payload['connectivity_risk']['streak_length'], 2)
        self.assertEqual(payload['connectivity_risk']['latest_action'], 'rd001_recent')
        self.assertIn('reachability', payload['connectivity_risk']['headline'].lower())

    def test_u001_ops_automation_api_surfaces_connectivity_recovery(self):
        self._seed_u001_connectivity_fixture()
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / 'runner_status.txt'
            self._write_runner_status(
                status_path,
                state='sleeping',
                updated_at=(timezone.now() - timedelta(minutes=1)).isoformat(),
                last_cycle_completed_at=(timezone.now() - timedelta(minutes=1)).isoformat(),
                last_exit_code='0',
            )
            with self.settings(U001_RD001_RECENT_RUNNER_STATUS_FILE=str(status_path)):
                response = self.client.get(reverse('u001_ops_automation_api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['connectivity_risk']['state'], 'healthy')
        self.assertTrue(payload['connectivity_risk']['recovered'])
        self.assertIn('recovered', payload['connectivity_risk']['headline'].lower())

    def test_u001_ops_automation_api_surfaces_spin_risk_warning(self):
        self._seed_u001_automation_spin_fixture()

        response = self.client.get(reverse('u001_ops_automation_api'), {'limit': 10})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['spin_risk']['state'], 'critical')
        self.assertEqual(payload['spin_risk']['action'], 'pool_mapping_recent')
        self.assertIn('spinning', payload['spin_risk']['headline'].lower())
        self.assertIn('mapped nothing', payload['spin_risk']['detail'].lower())

    def test_u001_ops_coverage_view_renders_funnel(self):
        self._seed_u001_coverage_queue_fixture()

        response = self.client.get(reverse('u001_ops_coverage'), {'preset': '14d'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'U-001 Coverage Funnel')
        self.assertContains(response, 'Funnel Stages')
        self.assertContains(response, 'Pool mapped')

    def test_u001_ops_coverage_api_returns_expected_stage_counts(self):
        self._seed_u001_coverage_queue_fixture()

        response = self.client.get(reverse('u001_ops_coverage_api'), {'preset': '14d'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        stages = {
            row['key']: row
            for row in payload['stages']
        }

        self.assertEqual(payload['discovered_count'], 6)
        self.assertEqual(stages['mapped']['count'], 5)
        self.assertEqual(stages['rd001_status']['count'], 4)
        self.assertEqual(stages['rd001_complete']['count'], 0)

    def test_u001_ops_queues_view_renders_lane_commands(self):
        self._seed_u001_coverage_queue_fixture()

        response = self.client.get(reverse('u001_ops_queues'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Recent safe Shyft steady-state lane')
        self.assertContains(response, './scripts/run_batch_partials_historical.sh --max-coins 3')
        self.assertContains(
            response,
            'MARJON_U001_RD001_MAX_FILTERED_SIGNATURES=1400 ./scripts/run_batch_partials_guarded.sh',
        )

    def test_u001_ops_queues_api_returns_expected_lanes(self):
        self._seed_u001_coverage_queue_fixture()

        response = self.client.get(reverse('u001_ops_queues_api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        queues = {
            queue['key']: queue
            for queue in payload['queues']
        }

        self.assertEqual(len(queues['recent_safe']['items']), 1)
        self.assertEqual(len(queues['recent_risky']['items']), 1)
        self.assertEqual(len(queues['historical_partial']['items']), 1)
        self.assertEqual(len(queues['historical_guarded']['items']), 1)
        self.assertEqual(len(queues['error_lane']['items']), 1)
        self.assertEqual(
            queues['historical_guarded']['items'][0]['guard_count'],
            1082,
        )

    def test_u001_ops_coin_view_renders_debug_sections(self):
        coin = self._seed_u001_coin_detail_fixture()

        response = self.client.get(reverse('u001_ops_coin', args=[coin.mint_address]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'U-001 Coin Detail')
        self.assertContains(response, coin.mint_address)
        self.assertContains(response, 'Recent Run History')
        self.assertContains(response, 'Skipped transaction rows')

    def test_u001_ops_coin_api_returns_expected_detail(self):
        coin = self._seed_u001_coin_detail_fixture()

        response = self.client.get(reverse('u001_ops_coin_api', args=[coin.mint_address]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['coin']['mint'], coin.mint_address)
        self.assertEqual(payload['warehouse_counts']['raw_transaction_count'], 1)
        self.assertEqual(payload['warehouse_counts']['skipped_transaction_count'], 1)
        self.assertEqual(payload['layer_statuses'][3]['status'], 'error')
        self.assertEqual(payload['run_history'][0]['layer_id'], 'RD-001')

    def test_u001_ops_trends_view_renders_sections(self):
        self._seed_u001_trends_fixture()

        response = self.client.get(reverse('u001_ops_trends'), {'days': 7})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'U-001 Trends')
        self.assertContains(response, 'Current Pressure')
        self.assertContains(response, 'Current Truth Audits')
        self.assertContains(response, 'Truth Audit Coverage')
        self.assertContains(response, 'Daily Truth Audits')
        self.assertContains(response, 'Daily Batch Outcomes')

    def test_u001_ops_trends_api_returns_expected_metrics(self):
        self._seed_u001_trends_fixture()

        response = self.client.get(reverse('u001_ops_trends_api'), {'days': 7})

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload['days'], 7)
        self.assertEqual(payload['current_highlights'][0]['label'], 'RD-001 transport statuses')
        self.assertEqual(payload['current_highlights'][0]['value'], 1)
        self.assertEqual(payload['current_highlights'][1]['value'], 1)
        self.assertEqual(len(payload['truth_audits']), 3)
        self.assertEqual(payload['truth_audits'][0]['status'], 'ok')
        self.assertEqual(payload['truth_audits'][1]['status'], 'warning')
        self.assertEqual(payload['truth_audits'][2]['status'], 'ok')
        self.assertEqual(payload['truth_audit_summary']['days_with_any'], 2)
        self.assertEqual(payload['truth_audit_summary']['days_without_any'], 5)
        self.assertEqual(payload['truth_audit_summary']['days_with_full'], 0)
        self.assertEqual(payload['truth_audit_summary']['days_with_findings'], 0)
        self.assertEqual(payload['truth_audit_summary']['days_with_warnings'], 1)
        self.assertTrue(any(row['has_snapshot'] for row in payload['daily_rows']))
        self.assertTrue(any(row['rd001_partial_count'] == 1 for row in payload['daily_rows']))
        self.assertTrue(any(row['rd001_transport_errors'] == 1 for row in payload['daily_rows']))
        self.assertTrue(any(row['fl002_auth_errors'] == 1 for row in payload['daily_rows']))
        self.assertTrue(any(row['source_audit_runs'] == 1 for row in payload['daily_rows']))
        self.assertTrue(any(row['rd001_chain_audit_warning_runs'] == 1 for row in payload['daily_rows']))
        self.assertTrue(any(row['fl001_derived_audit_ok'] == 1 for row in payload['daily_rows']))

    def _seed_u001_overview_fixture(self):
        now = timezone.now()
        coins = []
        for index in range(6):
            coins.append(MigratedCoin.objects.create(
                mint_address=f'MINT{index}',
                symbol=f'C{index}',
                anchor_event=now - timedelta(hours=index + 1),
            ))

        PoolMapping.objects.create(
            coin=coins[0],
            pool_address='POOL0',
            dex='pumpswap',
            source='fixture',
        )
        OHLCVCandle.objects.create(
            coin=coins[0],
            timestamp=now - timedelta(minutes=30),
            open_price=Decimal('1'),
            high_price=Decimal('2'),
            low_price=Decimal('1'),
            close_price=Decimal('1.5'),
            volume=Decimal('10'),
        )
        HolderSnapshot.objects.create(
            coin=coins[1],
            timestamp=now - timedelta(minutes=20),
            total_holders=100,
        )
        RawTransaction.objects.create(
            coin=coins[0],
            timestamp=now - timedelta(minutes=10),
            tx_signature='sig-0',
            trade_type='BUY',
            wallet_address='wallet-0',
            token_amount=1000,
            sol_amount=500,
            pool_address='POOL0',
            tx_fee=Decimal('0.01'),
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=coins[0],
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(minutes=5),
            last_error='transport_error: Server disconnected without sending a response.',
        )
        U001PipelineStatus.objects.create(
            coin=coins[1],
            layer_id='FL-002',
            status='error',
            last_run_at=now - timedelta(minutes=5),
            last_error='401 Unauthorized from Moralis',
        )
        PipelineBatchRun.objects.create(
            pipeline_id='U-001',
            mode='steady_state',
            status='complete',
            started_at=now - timedelta(minutes=15),
            completed_at=now - timedelta(minutes=10),
            coins_attempted=2,
            coins_succeeded=1,
            coins_failed=1,
            api_calls=7,
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=2),
            last_action='rd001_recent',
            last_action_reason='Safe recent RD-001 candidates are available inside Shyft retention.',
            last_action_status='complete',
            last_action_started_at=now - timedelta(minutes=2),
            last_action_completed_at=now - timedelta(minutes=1),
            last_snapshot_date=timezone.localdate(now),
            guarded_attempts_date=timezone.localdate(now),
            guarded_attempts_today=1,
            consecutive_failures=0,
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=2),
            completed_at=now - timedelta(minutes=1),
            action='rd001_recent',
            reason='Safe recent RD-001 candidates are available inside Shyft retention.',
            status='complete',
            command='fetch_transactions_batch',
            command_kwargs={'max_coins': 25},
            result_summary={'records_loaded': 321, 'records_skipped': 2, 'api_calls': 7, 'succeeded': 1, 'failed': 0},
            repaired_state=True,
            snapshot_taken=True,
        )
        U001BootRecoveryRun.objects.create(
            started_at=now - timedelta(minutes=12),
            completed_at=now - timedelta(minutes=11),
            status='complete',
            db_reachable=True,
            migrations_ok=True,
            automation_tick_started=True,
            automation_tick_status='complete',
            log_path='/tmp/recover.log',
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=2) + timedelta(minutes=2),
            status='warning',
            options={'sample_fl001': 1, 'sample_fl002': 1, 'sample_rd001': 1},
            finding_count=0,
            warning_count=1,
            summary={
                'discovery': {'status': 'ok', 'lag_hours': 0.0},
                'layers': {
                    'fl001': [{'status': 'ok', 'detail': 'FL-001 sample matched for MINT0'}],
                    'fl002': [{'status': 'warning', 'detail': 'FL-002 only found 0 informative sample(s) out of the 1 requested.'}],
                    'rd001': [{'status': 'ok', 'detail': 'RD-001 sample matched for MINT0'}],
                },
                'findings': [],
                'warnings': ['FL-002 only found 0 informative sample(s) out of the 1 requested.'],
            },
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=1),
            status='warning',
            options={
                'sample_coins': 1,
                'txs_per_coin': 1,
                'rpc_url': 'https://api.mainnet-beta.solana.com',
                'rpc_source': 'public_fallback',
            },
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=1,
            summary={
                'findings': [],
                'warnings': ['Direct-RPC window scan could not complete for MINT0 over fixture window: rate_limited_429'],
                'aggregate': {'statuses': {'ok': 1}, 'finding_buckets': {}, 'warning_buckets': {}},
                'window_aggregate': {'statuses': {'warning': 1}, 'finding_buckets': {}, 'warning_buckets': {'window_scan_failed': 1}},
            },
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(minutes=40),
            completed_at=now - timedelta(minutes=39),
            status='ok',
            options={'sample_coins': 1, 'hours': 1, 'sol_symbol': 'SOLUSDT'},
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={
                'findings': [],
                'warnings': [],
                'aggregate': {'statuses': {'ok': 1}, 'finding_buckets': {}, 'warning_buckets': {}},
            },
        )

    def _write_runner_status(self, path, **overrides):
        now = timezone.now().isoformat()
        payload = {
            'pid': str(os.getpid()),
            'state': 'running',
            'cycle': '3',
            'updated_at': now,
            'sleep_seconds': '120',
            'error_sleep_seconds': '300',
            'last_log_file': '/tmp/u001_rd001_recent_cycle3.log',
            'last_exit_code': '0',
        }
        payload.update({key: str(value) for key, value in overrides.items()})
        path.write_text('\n'.join(f'{key}={value}' for key, value in payload.items()))

    def _seed_u001_policy_pause_fixture(self):
        now = timezone.now()
        coin = MigratedCoin.objects.create(
            mint_address='PAUSE1',
            symbol='PAUSE1',
            anchor_event=now - timedelta(hours=6),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL-PAUSE',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=now - timedelta(minutes=10),
            tx_signature='pause-sig',
            trade_type='BUY',
            wallet_address='wallet-pause',
            token_amount=100,
            sol_amount=50,
            pool_address='POOL-PAUSE',
            tx_fee=Decimal('0.01'),
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(minutes=5),
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=2),
            last_action='rd001_recent',
            last_action_reason='Previous tick failed on recent RD-001 transport.',
            last_action_status='error',
            last_action_started_at=now - timedelta(minutes=3),
            last_action_completed_at=now - timedelta(minutes=2),
            consecutive_failures=3,
            notes='transport_error: network error',
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=2),
            completed_at=now - timedelta(minutes=2),
            action='rd001_recent',
            reason='Controller is in cooldown after repeated failed automation ticks. Wait for the failure pause window or force a lane manually.',
            status='error',
            command='fetch_transactions_batch',
            command_kwargs={'max_coins': 25},
            repaired_state=True,
            snapshot_taken=False,
            notes='transport_error: network error',
        )

    def _seed_u001_recent_pool_mapping_fixture(self):
        now = timezone.now()
        for index in range(5):
            coin = MigratedCoin.objects.create(
                mint_address=f'POOLREC{index}',
                symbol=f'P{index}',
                anchor_event=now - timedelta(hours=index + 1),
            )
            if index == 0:
                PoolMapping.objects.create(
                    coin=coin,
                    pool_address='POOL-RECENT-MAPPED',
                    dex='pumpswap',
                    source='fixture',
                )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(hours=1),
            last_action='pool_mapping_recent',
            last_action_reason='Recent discovery exists, but recent pool mapping still needs dedicated catch-up throughput.',
            last_action_status='complete',
            last_action_started_at=now - timedelta(hours=1),
            last_action_completed_at=now - timedelta(hours=1) + timedelta(minutes=1),
            last_snapshot_date=timezone.localdate(now),
            consecutive_failures=0,
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=1),
            action='pool_mapping_recent',
            reason='Recent discovery exists, but recent pool mapping still needs dedicated catch-up throughput.',
            status='complete',
            command='orchestrate',
            command_kwargs={'universe': 'u001', 'steps': 'pool_mapping', 'days': 3, 'coins': 1000},
            repaired_state=True,
            snapshot_taken=False,
        )

    def _seed_u001_coverage_queue_fixture(self):
        now = timezone.now()
        old_base = SHYFT_RETENTION_DAYS + 2
        recent_safe = MigratedCoin.objects.create(
            mint_address='SAFE1',
            symbol='SAFE1',
            anchor_event=now - timedelta(hours=6),
        )
        recent_risky = MigratedCoin.objects.create(
            mint_address='RISK1',
            symbol='RISK1',
            anchor_event=now - timedelta(hours=12),
        )
        recent_unmapped = MigratedCoin.objects.create(
            mint_address='UNMAP1',
            symbol='UNMAP1',
            anchor_event=now - timedelta(hours=20),
        )
        historical_partial = MigratedCoin.objects.create(
            mint_address='HISTP1',
            symbol='HISTP1',
            anchor_event=now - timedelta(days=old_base),
        )
        historical_guarded = MigratedCoin.objects.create(
            mint_address='HISTG1',
            symbol='HISTG1',
            anchor_event=now - timedelta(days=old_base + 1),
        )
        historical_error = MigratedCoin.objects.create(
            mint_address='HISTE1',
            symbol='HISTE1',
            anchor_event=now - timedelta(days=old_base + 2),
        )

        for coin, pool in (
            (recent_safe, 'POOL-SAFE'),
            (recent_risky, 'POOL-RISK'),
            (historical_partial, 'POOL-HISTP'),
            (historical_guarded, 'POOL-HISTG'),
            (historical_error, 'POOL-HISTE'),
        ):
            PoolMapping.objects.create(
                coin=coin,
                pool_address=pool,
                dex='pumpswap',
                source='fixture',
            )

        OHLCVCandle.objects.create(
            coin=recent_safe,
            timestamp=now - timedelta(minutes=45),
            open_price=Decimal('1'),
            high_price=Decimal('2'),
            low_price=Decimal('1'),
            close_price=Decimal('1.4'),
            volume=Decimal('8'),
        )
        OHLCVCandle.objects.create(
            coin=historical_partial,
            timestamp=now - timedelta(days=old_base - 1),
            open_price=Decimal('1'),
            high_price=Decimal('2'),
            low_price=Decimal('1'),
            close_price=Decimal('1.3'),
            volume=Decimal('7'),
        )
        HolderSnapshot.objects.create(
            coin=recent_risky,
            timestamp=now - timedelta(minutes=30),
            total_holders=50,
        )
        RawTransaction.objects.create(
            coin=recent_safe,
            timestamp=now - timedelta(minutes=15),
            tx_signature='safe-sig',
            trade_type='BUY',
            wallet_address='wallet-safe',
            token_amount=100,
            sol_amount=50,
            pool_address='POOL-SAFE',
            tx_fee=Decimal('0.01'),
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=0,
        )
        U001PipelineStatus.objects.create(
            coin=recent_safe,
            layer_id='FL-001',
            status='window_complete',
            last_run_at=now - timedelta(minutes=20),
        )
        U001PipelineStatus.objects.create(
            coin=recent_risky,
            layer_id='FL-002',
            status='error',
            last_run_at=now - timedelta(minutes=18),
            last_error='401 Unauthorized from Moralis',
        )
        U001PipelineStatus.objects.create(
            coin=recent_safe,
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(minutes=10),
            last_error='transport_error: Server disconnected without sending a response.',
        )
        U001PipelineStatus.objects.create(
            coin=historical_partial,
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(days=1),
        )
        U001PipelineStatus.objects.create(
            coin=historical_guarded,
            layer_id='RD-001',
            status='error',
            last_run_at=now - timedelta(days=2),
            last_error='Filtered signature count 1082 exceeds free-tier guard (1000)',
        )
        U001PipelineStatus.objects.create(
            coin=historical_error,
            layer_id='RD-001',
            status='error',
            last_run_at=now - timedelta(days=3),
            last_error='transport_error: network error',
        )

    def _seed_u001_automation_spin_fixture(self):
        now = timezone.now()
        for index in range(5):
            MigratedCoin.objects.create(
                mint_address=f'SPINPOOL{index}',
                symbol=f'SP{index}',
                anchor_event=now - timedelta(hours=index + 1),
            )

        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=2),
            last_action='pool_mapping_recent',
            last_action_reason='Recent discovery exists, but recent pool mapping still needs dedicated catch-up throughput.',
            last_action_status='complete',
            last_action_started_at=now - timedelta(minutes=2),
            last_action_completed_at=now - timedelta(minutes=1),
            last_snapshot_date=timezone.localdate(now),
            consecutive_failures=0,
        )
        for offset in range(4):
            U001AutomationTick.objects.create(
                started_at=now - timedelta(minutes=20 - offset),
                completed_at=now - timedelta(minutes=19 - offset),
                action='pool_mapping_recent',
                reason='Recent discovery exists, but recent pool mapping still needs dedicated catch-up throughput.',
                status='complete',
                command='orchestrate',
                command_kwargs={'universe': 'u001', 'steps': 'pool_mapping', 'days': 3, 'coins': 1000},
                result_summary={
                    'universe': 'U-001',
                    'dry_run': False,
                    'loops': 1,
                    'total_succeeded': 0,
                    'total_failed': 0,
                    'total_skipped': 0,
                    'steps': {
                        'pool_mapping': {
                            'mode': 'batch',
                            'mapped': 0,
                            'unmapped': 25,
                            'failed': 0,
                            'skipped': 0,
                        },
                    },
                },
                repaired_state=True,
                snapshot_taken=False,
            )

    def _seed_u001_connectivity_fixture(self):
        now = timezone.now()
        coin = MigratedCoin.objects.create(
            mint_address='NETDOWN1',
            symbol='NET1',
            anchor_event=now - timedelta(hours=6),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL-NETDOWN',
            dex='pumpswap',
            source='fixture',
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=2),
            last_action='rd001_recent',
            last_action_reason='Safe recent RD-001 candidates are available inside Shyft retention.',
            last_action_status='error',
            last_action_started_at=now - timedelta(minutes=2),
            last_action_completed_at=now - timedelta(minutes=1),
            last_snapshot_date=timezone.localdate(now),
            consecutive_failures=2,
            notes='transport_error: network error while contacting provider',
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=2),
            completed_at=now - timedelta(minutes=1),
            action='rd001_recent',
            reason='Safe recent RD-001 candidates are available inside Shyft retention.',
            status='error',
            command='fetch_transactions_batch',
            command_kwargs={'max_coins': 25},
            repaired_state=True,
            snapshot_taken=False,
            notes='transport_error: network error while contacting provider',
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=12),
            completed_at=now - timedelta(minutes=11),
            action='truth_source_audit',
            reason='Recent provider-source truth coverage is stale or missing.',
            status='error',
            command='audit_u001_sources',
            command_kwargs={},
            repaired_state=True,
            snapshot_taken=False,
            notes='transport_error: Temporary failure in name resolution',
        )

    def _seed_u001_coin_detail_fixture(self):
        now = timezone.now()
        coin = MigratedCoin.objects.create(
            mint_address='DETAIL1',
            symbol='DTL1',
            name='Detail Coin',
            anchor_event=now - timedelta(days=5),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL-DETAIL',
            dex='pumpswap',
            source='fixture',
        )
        OHLCVCandle.objects.create(
            coin=coin,
            timestamp=now - timedelta(days=4, minutes=30),
            open_price=Decimal('1'),
            high_price=Decimal('2'),
            low_price=Decimal('1'),
            close_price=Decimal('1.1'),
            volume=Decimal('4'),
        )
        HolderSnapshot.objects.create(
            coin=coin,
            timestamp=now - timedelta(days=4, minutes=20),
            total_holders=40,
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=now - timedelta(days=4, minutes=10),
            tx_signature='detail-sig',
            trade_type='BUY',
            wallet_address='wallet-detail',
            token_amount=100,
            sol_amount=20,
            pool_address='POOL-DETAIL',
            tx_fee=Decimal('0.01'),
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=0,
        )
        SkippedTransaction.objects.create(
            coin=coin,
            timestamp=now - timedelta(days=4, minutes=5),
            tx_signature='detail-skip',
            pool_address='POOL-DETAIL',
            tx_type='SWAP',
            tx_status='Success',
            skip_reason='parse_error',
            raw_json={'note': 'fixture'},
        )
        batch = PipelineBatchRun.objects.create(
            pipeline_id='U-001',
            mode='steady_state',
            status='complete',
            started_at=now - timedelta(days=1, minutes=15),
            completed_at=now - timedelta(days=1, minutes=10),
            coins_attempted=1,
            coins_succeeded=1,
            coins_failed=0,
            api_calls=5,
        )
        run = U001PipelineRun.objects.create(
            coin=coin,
            batch=batch,
            layer_id='RD-001',
            mode='steady_state',
            status='error',
            started_at=now - timedelta(days=1, minutes=15),
            completed_at=now - timedelta(days=1, minutes=12),
            records_loaded=1,
            api_calls=5,
            error_message='transport_error: network error',
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='FL-001',
            status='window_complete',
            watermark=now - timedelta(days=4, minutes=30),
            last_run_at=now - timedelta(days=1, hours=2),
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='FL-002',
            status='partial',
            watermark=now - timedelta(days=4, minutes=20),
            last_run_at=now - timedelta(days=1, hours=1),
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status='error',
            watermark=now - timedelta(days=4, minutes=10),
            last_run_at=now - timedelta(days=1, minutes=15),
            last_error='transport_error: network error',
            last_run=run,
        )
        return coin

    def _seed_u001_trends_fixture(self):
        now = timezone.now()
        coin = MigratedCoin.objects.create(
            mint_address='TREND1',
            symbol='TRD1',
            anchor_event=now - timedelta(days=4),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL-TREND',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='FL-002',
            status='error',
            last_run_at=now - timedelta(days=1),
            last_error='401 Unauthorized from Moralis',
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status='partial',
            last_run_at=now - timedelta(days=1),
            last_error='transport_error: network error',
        )
        second_coin = MigratedCoin.objects.create(
            mint_address='TREND2',
            symbol='TRD2',
            anchor_event=now - timedelta(days=5),
        )
        PoolMapping.objects.create(
            coin=second_coin,
            pool_address='POOL-TREND2',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=second_coin,
            layer_id='RD-001',
            status='error',
            last_run_at=now - timedelta(days=2),
            last_error='Filtered signature count 1082 exceeds free-tier guard (1000)',
        )

        batch_complete = PipelineBatchRun.objects.create(
            pipeline_id='U-001',
            mode='steady_state',
            status='complete',
            started_at=now - timedelta(days=1, hours=2),
            completed_at=now - timedelta(days=1, hours=1, minutes=30),
            coins_attempted=2,
            coins_succeeded=1,
            coins_failed=1,
            api_calls=9,
        )
        batch_error = PipelineBatchRun.objects.create(
            pipeline_id='U-001',
            mode='refill',
            status='error',
            started_at=now - timedelta(days=2, hours=3),
            completed_at=now - timedelta(days=2, hours=2, minutes=50),
            coins_attempted=1,
            coins_succeeded=0,
            coins_failed=1,
            api_calls=4,
            error_message='transport_error: network error',
        )

        U001PipelineRun.objects.create(
            coin=coin,
            batch=batch_complete,
            layer_id='FL-001',
            mode='steady_state',
            status='complete',
            started_at=now - timedelta(days=1, hours=2),
            completed_at=now - timedelta(days=1, hours=1, minutes=55),
            records_loaded=12,
            api_calls=2,
        )
        U001PipelineRun.objects.create(
            coin=coin,
            batch=batch_complete,
            layer_id='FL-002',
            mode='steady_state',
            status='error',
            started_at=now - timedelta(days=1, hours=2),
            completed_at=now - timedelta(days=1, hours=1, minutes=50),
            records_loaded=0,
            api_calls=3,
            error_message='401 Unauthorized from Moralis',
        )
        U001PipelineRun.objects.create(
            coin=coin,
            batch=batch_complete,
            layer_id='RD-001',
            mode='steady_state',
            status='complete',
            started_at=now - timedelta(days=1, hours=2),
            completed_at=now - timedelta(days=1, hours=1, minutes=45),
            records_loaded=50,
            api_calls=4,
        )
        U001PipelineRun.objects.create(
            coin=second_coin,
            batch=batch_error,
            layer_id='RD-001',
            mode='refill',
            status='error',
            started_at=now - timedelta(days=2, hours=3),
            completed_at=now - timedelta(days=2, hours=2, minutes=55),
            records_loaded=0,
            api_calls=4,
            error_message='transport_error: network error',
        )
        U001OpsSnapshot.objects.create(
            snapshot_date=(now - timedelta(days=1)).date(),
            discovered_count=2,
            mapped_count=2,
            fl001_complete_count=0,
            fl002_complete_count=0,
            rd001_complete_count=0,
            rd001_partial_count=1,
            rd001_error_count=1,
            rd001_transport_error_count=1,
            rd001_guard_error_count=1,
            fl002_auth_error_count=1,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(days=1, hours=4),
            completed_at=now - timedelta(days=1, hours=3, minutes=55),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={
                'discovery': {'status': 'ok'},
                'layers': {
                    'FL-001': {'status': 'ok'},
                    'FL-002': {'status': 'ok'},
                    'RD-001': {'status': 'ok'},
                },
            },
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(days=2, hours=4),
            completed_at=now - timedelta(days=2, hours=3, minutes=45),
            status='warning',
            transaction_count=2,
            finding_count=0,
            warning_count=1,
            summary={
                'aggregate': {
                    'statuses': {'ok': 1, 'warning': 1},
                    'warning_buckets': {'rate_limited_429': 1},
                },
                'warnings': ['window scan hit rate_limited_429'],
            },
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(days=1, hours=5),
            completed_at=now - timedelta(days=1, hours=4, minutes=50),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={
                'aggregate': {
                    'statuses': {'ok': 1},
                    'warning_buckets': {},
                },
            },
        )
