"""Tests for warehouse management commands."""

import io
import os
from pathlib import Path
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from warehouse.models import (
    BinanceAsset,
    HolderSnapshot,
    MigratedCoin,
    OHLCVCandle,
    PipelineBatchRun,
    PipelineCompleteness,
    PoolMapping,
    RawTransaction,
    RunMode,
    RunStatus,
    U001AutomationState,
    U001AutomationTick,
    U001BootRecoveryRun,
    U001FL001DerivedAuditRun,
    U001OpsSnapshot,
    U001PipelineRun,
    U001PipelineStatus,
    U001RD001ChainAuditRun,
    U001SourceAuditRun,
    U002OHLCVCandle,
)

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


class U001IngestionHealthCommandTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='HEALTH_CMD',
            anchor_event=T0,
        )
        self.guard_coin = MigratedCoin.objects.create(
            mint_address='HEALTH_GUARD_CMD',
            anchor_event=T0,
        )
        self.transport_coin = MigratedCoin.objects.create(
            mint_address='HEALTH_TRANSPORT_CMD',
            anchor_event=T0,
        )
        old_time = dj_timezone.now() - timedelta(days=10)
        MigratedCoin.objects.filter(pk=self.coin.pk).update(ingested_at=old_time)
        MigratedCoin.objects.filter(pk=self.guard_coin.pk).update(ingested_at=old_time)
        MigratedCoin.objects.filter(pk=self.transport_coin.pk).update(ingested_at=old_time)

        OHLCVCandle.objects.create(
            coin_id='HEALTH_CMD',
            timestamp=T0,
            open_price=1,
            high_price=1,
            low_price=1,
            close_price=1,
            volume=1,
        )
        OHLCVCandle.objects.filter(coin_id='HEALTH_CMD').update(ingested_at=old_time)

        HolderSnapshot.objects.create(
            coin_id='HEALTH_CMD',
            timestamp=T0,
            total_holders=100,
            net_holder_change=1,
            holder_percent_change=1,
            acquired_via_swap=1,
            acquired_via_transfer=0,
            acquired_via_airdrop=0,
            holders_in_whales=0,
            holders_in_sharks=0,
            holders_in_dolphins=0,
            holders_in_fish=0,
            holders_in_octopus=0,
            holders_in_crabs=0,
            holders_in_shrimps=0,
            holders_out_whales=0,
            holders_out_sharks=0,
            holders_out_dolphins=0,
            holders_out_fish=0,
            holders_out_octopus=0,
            holders_out_crabs=0,
            holders_out_shrimps=0,
        )
        HolderSnapshot.objects.filter(coin_id='HEALTH_CMD').update(ingested_at=old_time)

        RawTransaction.objects.create(
            coin_id='HEALTH_CMD',
            timestamp=T0,
            tx_signature='SIG_HEALTH_1',
            trade_type='BUY',
            wallet_address='WALLET_HEALTH',
            token_amount=100,
            sol_amount=50,
            pool_address='POOL_HEALTH',
            tx_fee='0.001',
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=1,
            pool_token_reserves=10,
            pool_sol_reserves=10,
        )
        RawTransaction.objects.filter(coin_id='HEALTH_CMD').update(ingested_at=old_time)

        U001PipelineStatus.objects.create(
            coin_id='HEALTH_CMD',
            layer_id='FL-002',
            status=PipelineCompleteness.ERROR,
            last_error="Client error '401 Unauthorized' for url 'https://solana-gateway.moralis.io/...'",
            last_run_at=old_time,
        )
        U001PipelineStatus.objects.create(
            coin_id='HEALTH_CMD',
            layer_id='RD-001',
            status=PipelineCompleteness.IN_PROGRESS,
            last_run_at=old_time,
        )
        U001PipelineStatus.objects.create(
            coin_id='HEALTH_GUARD_CMD',
            layer_id='RD-001',
            status=PipelineCompleteness.PARTIAL,
            last_error=(
                "Filtered signature count 1789 exceeds free-tier guard "
                "(1000) for pool POOL_HEALTH"
            ),
            last_run_at=old_time,
        )
        U001PipelineStatus.objects.create(
            coin_id='HEALTH_TRANSPORT_CMD',
            layer_id='RD-001',
            status=PipelineCompleteness.ERROR,
            last_error=(
                'Failed after 3 retries: https://rpc.shyft.to?api_key=test '
                '(last_error: transport_error: RemoteProtocolError: '
                'Server disconnected without sending a response.)'
            ),
            last_run_at=old_time,
        )
        U001PipelineStatus.objects.create(
            coin_id='HEALTH_CMD',
            layer_id='FL-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
            last_run_at=old_time,
        )

        PipelineBatchRun.objects.create(
            pipeline_id='U-001',
            mode=RunMode.STEADY_STATE,
            status=RunStatus.COMPLETE,
            started_at=old_time,
            completed_at=old_time + timedelta(minutes=5),
            coins_succeeded=1,
            coins_failed=0,
        )

    def test_command_reports_staleness_and_blockers(self):
        out = io.StringIO()

        call_command(
            'u001_ingestion_health',
            stale_days=1,
            stale_in_progress_hours=1,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('U-001 INGESTION HEALTH', output)
        self.assertIn('FL-002 is seeing authentication failures', output)
        self.assertIn('RD-001 has 1 stale in_progress statuses', output)
        self.assertIn('free_tier_guarded_statuses: 1', output)
        self.assertIn('current_error_buckets: auth=1', output)
        self.assertIn('transport=1', output)
        self.assertIn('free_tier_guard=1', output)
        self.assertIn('latest_coin_ingested', output)

    def test_fail_on_blockers_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command(
                'u001_ingestion_health',
                stale_days=1,
                stale_in_progress_hours=1,
                fail_on_blockers=True,
                stdout=io.StringIO(),
            )


class RepairU001IngestionCommandTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='REPAIR_CMD',
            anchor_event=T0,
        )
        self.old_time = dj_timezone.now() - timedelta(hours=12)

        PipelineBatchRun.objects.create(
            pipeline_id='U-001',
            mode=RunMode.STEADY_STATE,
            status=RunStatus.STARTED,
            started_at=self.old_time,
        )
        self.run = U001PipelineRun.objects.create(
            coin=self.coin,
            layer_id='RD-001',
            mode=RunMode.STEADY_STATE,
            status=RunStatus.STARTED,
            started_at=self.old_time,
        )
        U001PipelineStatus.objects.create(
            coin=self.coin,
            layer_id='RD-001',
            status=PipelineCompleteness.IN_PROGRESS,
            last_run=self.run,
            last_run_at=self.old_time,
        )

    def test_dry_run_leaves_rows_unchanged(self):
        call_command(
            'repair_u001_ingestion',
            stale_hours=6,
            dry_run=True,
            stdout=io.StringIO(),
        )

        self.assertEqual(
            PipelineBatchRun.objects.get().status,
            RunStatus.STARTED,
        )
        self.assertEqual(
            U001PipelineRun.objects.get().status,
            RunStatus.STARTED,
        )
        self.assertEqual(
            U001PipelineStatus.objects.get().status,
            PipelineCompleteness.IN_PROGRESS,
        )

    def test_command_marks_stale_rows_as_error(self):
        call_command(
            'repair_u001_ingestion',
            stale_hours=6,
            stdout=io.StringIO(),
        )

        self.assertEqual(
            PipelineBatchRun.objects.get().status,
            RunStatus.ERROR,
        )
        run = U001PipelineRun.objects.get()
        self.assertEqual(run.status, RunStatus.ERROR)
        self.assertIn('Marked stale by repair_u001_ingestion', run.error_message)
        status = U001PipelineStatus.objects.get()
        self.assertEqual(status.status, PipelineCompleteness.ERROR)
        self.assertIn('Marked stale by repair_u001_ingestion', status.last_error)


class SnapshotU001OpsCommandTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='SNAP_CMD_1',
            anchor_event=T0,
        )
        self.guard_coin = MigratedCoin.objects.create(
            mint_address='SNAP_CMD_2',
            anchor_event=T0,
        )

        PoolMapping.objects.create(
            coin=self.coin,
            pool_address='POOL_SNAP_1',
            dex='pumpswap',
            source='fixture',
        )
        PoolMapping.objects.create(
            coin=self.guard_coin,
            pool_address='POOL_SNAP_2',
            dex='pumpswap',
            source='fixture',
        )

        U001PipelineStatus.objects.create(
            coin=self.coin,
            layer_id='FL-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001PipelineStatus.objects.create(
            coin=self.coin,
            layer_id='FL-002',
            status=PipelineCompleteness.ERROR,
            last_error="Client error '401 Unauthorized' for url 'https://solana-gateway.moralis.io/...'",
        )
        U001PipelineStatus.objects.create(
            coin=self.coin,
            layer_id='RD-001',
            status=PipelineCompleteness.PARTIAL,
            last_error='transport_error: network error',
        )
        U001PipelineStatus.objects.create(
            coin=self.guard_coin,
            layer_id='RD-001',
            status=PipelineCompleteness.ERROR,
            last_error='Filtered signature count 1082 exceeds free-tier guard (1000)',
        )

    def test_command_creates_snapshot_row(self):
        out = io.StringIO()

        call_command(
            'snapshot_u001_ops',
            date='2026-04-10',
            stdout=out,
        )

        snapshot = U001OpsSnapshot.objects.get(snapshot_date='2026-04-10')
        self.assertEqual(snapshot.discovered_count, 2)
        self.assertEqual(snapshot.mapped_count, 2)
        self.assertEqual(snapshot.fl001_complete_count, 1)
        self.assertEqual(snapshot.fl002_auth_error_count, 1)
        self.assertEqual(snapshot.rd001_partial_count, 1)
        self.assertEqual(snapshot.rd001_error_count, 1)
        self.assertEqual(snapshot.rd001_transport_error_count, 1)
        self.assertEqual(snapshot.rd001_guard_error_count, 1)
        self.assertIn('created snapshot for 2026-04-10', out.getvalue())

    def test_command_updates_existing_snapshot(self):
        U001OpsSnapshot.objects.create(
            snapshot_date='2026-04-10',
            discovered_count=999,
        )

        call_command(
            'snapshot_u001_ops',
            date='2026-04-10',
            stdout=io.StringIO(),
        )

        snapshot = U001OpsSnapshot.objects.get(snapshot_date='2026-04-10')
        self.assertEqual(snapshot.discovered_count, 2)


class RecoverU001AfterRebootCommandTest(TestCase):
    @patch('warehouse.management.commands.recover_u001_after_reboot.call_command')
    def test_command_records_successful_recovery_run(self, mock_call):
        out = io.StringIO()

        call_command(
            'recover_u001_after_reboot',
            log_path='/tmp/reboot.log',
            stdout=out,
        )

        run = U001BootRecoveryRun.objects.get()
        self.assertEqual(run.status, 'complete')
        self.assertTrue(run.db_reachable)
        self.assertTrue(run.migrations_ok)
        self.assertTrue(run.automation_tick_started)
        self.assertEqual(run.automation_tick_status, 'complete')
        self.assertEqual(run.log_path, '/tmp/reboot.log')
        self.assertEqual(
            [item.args[0] for item in mock_call.call_args_list],
            ['automate_u001'],
        )
        self.assertIn('recorded_boot_recovery: complete', out.getvalue())

    @patch('warehouse.management.commands.recover_u001_after_reboot.call_command')
    def test_command_records_failed_recovery_run(self, mock_call):
        mock_call.side_effect = RuntimeError('automation failed')

        with self.assertRaises(CommandError):
            call_command(
                'recover_u001_after_reboot',
                stdout=io.StringIO(),
            )

        run = U001BootRecoveryRun.objects.get()
        self.assertEqual(run.status, 'error')
        self.assertEqual(run.automation_tick_status, 'error')
        self.assertTrue(run.automation_tick_started)
        self.assertIn('automation failed', run.notes)


class AuditU001CommandTest(TestCase):
    def _write_runner_status(self, path, **overrides):
        payload = {
            'pid': str(os.getpid()),
            'state': 'sleeping',
            'cycle': '4',
            'updated_at': dj_timezone.now().isoformat(),
            'sleep_seconds': '120',
            'error_sleep_seconds': '300',
            'last_exit_code': '0',
            'last_log_file': '/tmp/u001_rd001_recent_cycle4.log',
        }
        payload.update({key: str(value) for key, value in overrides.items()})
        path.write_text('\n'.join(f'{key}={value}' for key, value in payload.items()))

    def test_command_reports_blockers_and_warnings(self):
        now = dj_timezone.now()
        coin = MigratedCoin.objects.create(
            mint_address='AUDIT_BAD',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        old_time = now - timedelta(days=3)
        MigratedCoin.objects.filter(pk=coin.pk).update(ingested_at=old_time)

        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL_AUDIT_BAD',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='FL-002',
            status=PipelineCompleteness.ERROR,
            last_error="Client error '401 Unauthorized' for url 'https://solana-gateway.moralis.io/...'",
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status=PipelineCompleteness.ERROR,
            last_error='transport_error: network error',
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            consecutive_failures=3,
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(hours=6),
            completed_at=now - timedelta(hours=6),
            action='rd001_recent',
            reason='fixture',
            status='error',
            repaired_state=True,
            snapshot_taken=False,
            notes='transport_error: network error',
        )
        U001OpsSnapshot.objects.create(
            snapshot_date=(dj_timezone.localdate(now) - timedelta(days=4)),
            discovered_count=1,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(days=3),
            completed_at=now - timedelta(days=3),
            status='finding',
            finding_count=1,
            warning_count=0,
            summary={'findings': ['fixture mismatch'], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(days=3),
            completed_at=now - timedelta(days=3),
            status='finding',
            options={'rpc_source': 'helius_api_key_1'},
            coin_count=1,
            transaction_count=1,
            finding_count=1,
            warning_count=0,
            summary={'findings': ['chain mismatch fixture'], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(days=3),
            completed_at=now - timedelta(days=3),
            status='finding',
            coin_count=1,
            candle_count=3,
            finding_count=1,
            warning_count=0,
            summary={'findings': ['derived mismatch fixture'], 'warnings': []},
        )

        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command('audit_u001', stdout=out)

        output = out.getvalue()
        self.assertIn('U-001 UNATTENDED SAFETY AUDIT', output)
        self.assertIn('Discovery is stale', output)
        self.assertIn('Automation is stale', output)
        self.assertIn('FL-002 has 1 current auth-failure statuses', output)
        self.assertIn('latest_boot_recovery: None', output)
        self.assertIn('latest_boot_recovery_status: None', output)
        self.assertIn('latest_boot_recovery_tick_status: None', output)
        self.assertIn('automation_connectivity_state: warn', output)
        self.assertIn('automation_connectivity_streak: 1', output)
        self.assertIn('automation_connectivity_action: rd001_recent', output)
        self.assertIn('latest_complete_action: None', output)
        self.assertIn('latest_complete_streak: 0', output)
        self.assertIn('latest_complete_streak_structured_ticks: 0/0', output)
        self.assertIn('latest_complete_streak_loaded_rows: n/a', output)
        self.assertIn('Automation currently looks blocked by internet or upstream reachability problems', output)
        self.assertIn('recent_mapped_pct_3d: n/a (0/0)', output)
        self.assertIn('Latest live source audit finding: fixture mismatch', output)
        self.assertIn('Latest RD-001 direct chain audit finding: chain mismatch fixture', output)
        self.assertIn('Latest FL-001 derived audit finding: derived mismatch fixture', output)
        self.assertIn('latest_rd001_chain_audit_status: finding', output)
        self.assertIn('latest_fl001_derived_audit_status: finding', output)
        self.assertIn('recent_truth_audit_window_days: 7', output)
        self.assertIn('recent_truth_audit_days_with_any: 1', output)
        self.assertIn('recent_truth_audit_days_without_any: 6', output)
        self.assertIn('recent_truth_audit_days_with_full: 1', output)
        self.assertIn('recent_truth_audit_days_with_findings: 1', output)
        self.assertIn('recent_truth_audit_days_with_warnings: 0', output)
        self.assertIn('external_truth_check: sampled live-source audit, sampled RD-001 direct chain audit, and sampled FL-001 derived audit available', output)

    def test_command_passes_for_healthy_fixture(self):
        now = dj_timezone.now()
        mature_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_GOOD',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        recent_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_GOOD_RECENT',
            anchor_event=now - timedelta(hours=2),
        )

        for coin, pool in (
            (mature_coin, 'POOL_AUDIT_GOOD'),
            (recent_coin, 'POOL_AUDIT_GOOD_RECENT'),
        ):
            PoolMapping.objects.create(
                coin=coin,
                pool_address=pool,
                dex='pumpswap',
                source='fixture',
            )

        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='FL-002',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=20),
            last_action='refresh_core',
            last_action_status='complete',
            last_action_completed_at=now - timedelta(minutes=19),
            consecutive_failures=0,
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=20),
            completed_at=now - timedelta(minutes=19),
            action='refresh_core',
            reason='fixture',
            status='complete',
            command='orchestrate',
            command_kwargs={'steps': 'discovery,pool_mapping,ohlcv'},
            repaired_state=True,
            snapshot_taken=True,
        )
        U001OpsSnapshot.objects.create(
            snapshot_date=dj_timezone.localdate(now),
            discovered_count=2,
            mapped_count=2,
            fl002_complete_count=1,
            rd001_complete_count=1,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=3),
            completed_at=now - timedelta(hours=3) + timedelta(minutes=5),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=2) + timedelta(minutes=5),
            status='ok',
            options={'rpc_source': 'helius_api_key_1'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / 'runner_status.txt'
            self._write_runner_status(status_path)
            with self.settings(U001_RD001_RECENT_RUNNER_STATUS_FILE=str(status_path)):
                call_command(
                    'audit_u001',
                    min_fl002_complete_pct=0.5,
                    min_rd001_complete_pct=0.5,
                    max_rd001_transport_statuses=1,
                    stdout=out,
                )

        output = out.getvalue()
        self.assertIn('No blockers detected.', output)
        self.assertIn('No warnings detected.', output)
        self.assertIn('rd001_recent_runner_state: sleeping', output)
        self.assertIn('rd001_recent_runner_pid_alive: True', output)
        self.assertIn('latest_boot_recovery: None', output)
        self.assertIn('latest_boot_recovery_status: None', output)
        self.assertIn('latest_boot_recovery_tick_status: None', output)
        self.assertIn('automation_connectivity_state: healthy', output)
        self.assertIn('automation_connectivity_streak: 0', output)
        self.assertIn('latest_complete_action: refresh_core', output)
        self.assertIn('latest_complete_streak: 1', output)
        self.assertIn('latest_complete_streak_structured_ticks: 0/1', output)
        self.assertIn('latest_complete_streak_loaded_rows: n/a', output)
        self.assertIn('latest_rd001_chain_audit_status: ok', output)
        self.assertIn('latest_fl001_derived_audit_status: ok', output)
        self.assertIn('recent_truth_audit_days_with_any: 1', output)
        self.assertIn('recent_truth_audit_days_without_any: 6', output)

    def test_command_reports_connectivity_recovery_when_runner_succeeds_after_outage(self):
        now = dj_timezone.now()
        mature_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_RECOVERED',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        recent_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_RECOVERED_RECENT',
            anchor_event=now - timedelta(hours=2),
        )
        for coin, pool in (
            (mature_coin, 'POOL_AUDIT_RECOVERED'),
            (recent_coin, 'POOL_AUDIT_RECOVERED_RECENT'),
        ):
            PoolMapping.objects.create(
                coin=coin,
                pool_address=pool,
                dex='pumpswap',
                source='fixture',
            )

        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='FL-002',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=10),
            last_action='rd001_partial_historical',
            last_action_status='complete',
            last_action_completed_at=now - timedelta(minutes=9),
            consecutive_failures=0,
            notes=None,
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=20),
            completed_at=now - timedelta(minutes=19),
            action='rd001_recent',
            reason='fixture',
            status='error',
            command='fetch_transactions_batch',
            command_kwargs={'max_coins': 25},
            notes='transport_error: Temporary failure in name resolution',
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=9),
            action='rd001_partial_historical',
            reason='fixture',
            status='complete',
            command='fetch_transactions_batch',
            command_kwargs={'source': 'helius', 'status_filter': 'partial', 'max_coins': 5},
            result_summary={
                'queued_coins': 5,
                'succeeded': 5,
                'failed': 0,
                'records_loaded': 25,
                'records_skipped': 0,
                'api_calls': 15,
            },
        )
        U001BootRecoveryRun.objects.create(
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=2) + timedelta(minutes=1),
            status='error',
            db_reachable=True,
            migrations_ok=True,
            automation_tick_started=True,
            automation_tick_status='error',
            notes='transport_error: Temporary failure in name resolution',
            log_path='/tmp/boot-recovery.log',
        )
        U001OpsSnapshot.objects.create(
            snapshot_date=dj_timezone.localdate(now),
            discovered_count=2,
            mapped_count=2,
            fl002_complete_count=1,
            rd001_complete_count=1,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=3),
            completed_at=now - timedelta(hours=3) + timedelta(minutes=5),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=2) + timedelta(minutes=5),
            status='ok',
            options={'rpc_source': 'helius_api_key_1'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / 'runner_status.txt'
            self._write_runner_status(
                status_path,
                state='sleeping',
                updated_at=(now - timedelta(minutes=1)).isoformat(),
                last_cycle_completed_at=(now - timedelta(minutes=1)).isoformat(),
                last_exit_code='0',
            )
            with self.settings(U001_RD001_RECENT_RUNNER_STATUS_FILE=str(status_path)):
                call_command(
                    'audit_u001',
                    min_fl002_complete_pct=0.5,
                    min_rd001_complete_pct=0.5,
                    max_rd001_transport_statuses=999,
                    stdout=out,
                )

        output = out.getvalue()
        self.assertIn('automation_connectivity_state: recovered', output)
        self.assertIn('automation_connectivity_recovered_at:', output)
        self.assertNotIn('Automation currently looks blocked by internet or upstream reachability problems', output)
        self.assertNotIn('Latest reboot recovery failed after DB startup', output)

    def test_command_warns_when_recent_truth_audit_coverage_is_missing(self):
        now = dj_timezone.now()
        mature_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_TRUTH_GAP',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        PoolMapping.objects.create(
            coin=mature_coin,
            pool_address='POOL_AUDIT_TRUTH_GAP',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='FL-002',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=20),
            last_action='refresh_core',
            last_action_status='complete',
            last_action_completed_at=now - timedelta(minutes=19),
            consecutive_failures=0,
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=20),
            completed_at=now - timedelta(minutes=19),
            action='refresh_core',
            reason='fixture',
            status='complete',
            command='orchestrate',
            command_kwargs={'steps': 'discovery,pool_mapping,ohlcv'},
            repaired_state=True,
            snapshot_taken=True,
        )
        U001OpsSnapshot.objects.create(
            snapshot_date=dj_timezone.localdate(now),
            discovered_count=1,
            mapped_count=1,
            fl002_complete_count=1,
            rd001_complete_count=1,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(days=10),
            completed_at=now - timedelta(days=10) + timedelta(minutes=5),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(days=10),
            completed_at=now - timedelta(days=10) + timedelta(minutes=5),
            status='ok',
            options={'rpc_source': 'helius_api_key_1'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(days=10),
            completed_at=now - timedelta(days=10) + timedelta(minutes=5),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        out = io.StringIO()
        call_command(
            'audit_u001',
            min_fl002_complete_pct=0.5,
            min_rd001_complete_pct=0.5,
            max_rd001_transport_statuses=1,
            max_source_audit_stale_days=30,
            max_rd001_chain_audit_stale_days=30,
            max_fl001_derived_audit_stale_days=30,
            truth_audit_window_days=7,
            min_truth_audit_days_with_any=1,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('recent_truth_audit_window_days: 7', output)
        self.assertIn('recent_truth_audit_days_with_any: 0', output)
        self.assertIn('recent_truth_audit_days_without_any: 7', output)
        self.assertIn(
            'Recent Phase 0 truth-audit coverage is too thin: 0/7 days had any truth-audit activity.',
            output,
        )

    def test_command_warns_when_recent_runner_heartbeat_is_missing(self):
        now = dj_timezone.now()
        mature_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_RUNNER_GAP',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        PoolMapping.objects.create(
            coin=mature_coin,
            pool_address='POOL_AUDIT_RUNNER_GAP',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='FL-002',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=20),
            last_action='refresh_core',
            last_action_status='complete',
            last_action_completed_at=now - timedelta(minutes=19),
            consecutive_failures=0,
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=20),
            completed_at=now - timedelta(minutes=19),
            action='refresh_core',
            reason='fixture',
            status='complete',
            command='orchestrate',
            command_kwargs={'steps': 'discovery,pool_mapping,ohlcv'},
            repaired_state=True,
            snapshot_taken=True,
        )
        U001OpsSnapshot.objects.create(
            snapshot_date=dj_timezone.localdate(now),
            discovered_count=1,
            mapped_count=1,
            fl002_complete_count=1,
            rd001_complete_count=1,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=3),
            completed_at=now - timedelta(hours=3) + timedelta(minutes=5),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=2) + timedelta(minutes=5),
            status='ok',
            options={'rpc_source': 'helius_api_key_1'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / 'missing_runner_status.txt'
            with self.settings(U001_RD001_RECENT_RUNNER_STATUS_FILE=str(missing_path)):
                call_command(
                    'audit_u001',
                    min_fl002_complete_pct=0.5,
                    min_rd001_complete_pct=0.5,
                    max_rd001_transport_statuses=1,
                    stdout=out,
                )

        output = out.getvalue()
        self.assertIn('Dedicated recent RD-001 runner has no heartbeat file yet.', output)
        self.assertIn(f'rd001_recent_runner_status_file: {missing_path}', output)
        self.assertIn('rd001_recent_runner_state: None', output)
        self.assertIn('rd001_recent_runner_pid_alive: False', output)

    def test_command_warns_when_latest_boot_recovery_failed(self):
        now = dj_timezone.now()
        mature_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_BOOT_FAIL',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        PoolMapping.objects.create(
            coin=mature_coin,
            pool_address='POOL_AUDIT_BOOT_FAIL',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='FL-002',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=20),
            last_action='refresh_core',
            last_action_status='complete',
            last_action_completed_at=now - timedelta(minutes=19),
            consecutive_failures=0,
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=20),
            completed_at=now - timedelta(minutes=19),
            action='refresh_core',
            reason='fixture',
            status='complete',
            command='orchestrate',
            command_kwargs={'steps': 'discovery,pool_mapping,ohlcv'},
            repaired_state=True,
            snapshot_taken=True,
        )
        U001BootRecoveryRun.objects.create(
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=9),
            status='error',
            db_reachable=True,
            migrations_ok=True,
            automation_tick_started=True,
            automation_tick_status='error',
            notes='transport timeout after DB recovery',
        )
        U001OpsSnapshot.objects.create(
            snapshot_date=dj_timezone.localdate(now),
            discovered_count=1,
            mapped_count=1,
            fl002_complete_count=1,
            rd001_complete_count=1,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            options={'rpc_source': 'helius_api_key_1'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        out = io.StringIO()
        call_command(
            'audit_u001',
            min_fl002_complete_pct=0.5,
            min_rd001_complete_pct=0.5,
            max_rd001_transport_statuses=1,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('latest_boot_recovery_status: error', output)
        self.assertIn('latest_boot_recovery_tick_status: error', output)
        self.assertIn('Latest reboot recovery failed after DB startup', output)
        self.assertIn('transport timeout after DB recovery', output)

    def test_command_warns_when_chain_audit_used_public_fallback(self):
        now = dj_timezone.now()
        mature_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_CHAIN_PUBLIC',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        PoolMapping.objects.create(
            coin=mature_coin,
            pool_address='POOL_AUDIT_CHAIN_PUBLIC',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='FL-002',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=20),
            last_action='refresh_core',
            last_action_status='complete',
            last_action_completed_at=now - timedelta(minutes=19),
            consecutive_failures=0,
        )
        U001AutomationTick.objects.create(
            started_at=now - timedelta(minutes=20),
            completed_at=now - timedelta(minutes=19),
            action='refresh_core',
            reason='fixture',
            status='complete',
            command='orchestrate',
            command_kwargs={'steps': 'discovery,pool_mapping,ohlcv'},
            repaired_state=True,
            snapshot_taken=True,
        )
        U001OpsSnapshot.objects.create(
            snapshot_date=dj_timezone.localdate(now),
            discovered_count=1,
            mapped_count=1,
            fl002_complete_count=1,
            rd001_complete_count=1,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='warning',
            options={'rpc_source': 'public_fallback'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=1,
            summary={'findings': [], 'warnings': ['window scan failed']},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        out = io.StringIO()
        call_command(
            'audit_u001',
            min_fl002_complete_pct=0.5,
            min_rd001_complete_pct=0.5,
            max_rd001_transport_statuses=200,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn(
            'Latest RD-001 direct chain audit used the public Solana RPC fallback',
            output,
        )

    def test_command_warns_when_recent_rd001_ticks_spin_without_loading_rows(self):
        now = dj_timezone.now()
        mature_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_SPIN_MATURE',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        recent_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_SPIN_RECENT',
            anchor_event=now - timedelta(hours=6),
        )

        for coin, pool in (
            (mature_coin, 'POOL_AUDIT_SPIN_MATURE'),
            (recent_coin, 'POOL_AUDIT_SPIN_RECENT'),
        ):
            PoolMapping.objects.create(
                coin=coin,
                pool_address=pool,
                dex='pumpswap',
                source='fixture',
            )

        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='FL-002',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=10),
            last_action='rd001_recent',
            last_action_status='complete',
            last_action_completed_at=now - timedelta(minutes=9),
            consecutive_failures=0,
        )
        for offset in range(3):
            U001AutomationTick.objects.create(
                started_at=now - timedelta(minutes=20 - offset),
                completed_at=now - timedelta(minutes=19 - offset),
                action='rd001_recent',
                reason='fixture',
                status='complete',
                command='fetch_transactions_batch',
                command_kwargs={'max_coins': 25},
                result_summary={
                    'records_loaded': 0,
                    'records_skipped': 0,
                    'api_calls': 3,
                    'succeeded': 0,
                    'failed': 0,
                },
                repaired_state=True,
                snapshot_taken=False,
            )
        U001OpsSnapshot.objects.create(
            snapshot_date=dj_timezone.localdate(now),
            discovered_count=2,
            mapped_count=2,
            fl002_complete_count=1,
            rd001_complete_count=1,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            options={'rpc_source': 'helius_api_key_1'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        out = io.StringIO()
        call_command(
            'audit_u001',
            min_fl002_complete_pct=0.5,
            min_rd001_complete_pct=0.5,
            max_rd001_transport_statuses=1,
            max_single_lane_streak=3,
            rd001_no_progress_ticks=3,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('latest_complete_action: rd001_recent', output)
        self.assertIn('latest_complete_streak: 3', output)
        self.assertIn('latest_complete_streak_structured_ticks: 3/3', output)
        self.assertIn('latest_complete_streak_loaded_rows: 0', output)
        self.assertIn('3 consecutive rd001_recent ticks with 0 total loaded rows', output)
        self.assertIn('without loading any rows', output)

    def test_command_warns_when_holders_ticks_only_skip_work(self):
        now = dj_timezone.now()
        mature_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_HOLDERS_SPIN_MATURE',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        PoolMapping.objects.create(
            coin=mature_coin,
            pool_address='POOL_AUDIT_HOLDERS_SPIN',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=mature_coin,
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=10),
            last_action='holders_catchup',
            last_action_status='complete',
            last_action_completed_at=now - timedelta(minutes=9),
            consecutive_failures=0,
        )
        for offset in range(4):
            U001AutomationTick.objects.create(
                started_at=now - timedelta(minutes=40 - offset),
                completed_at=now - timedelta(minutes=39 - offset),
                action='holders_catchup',
                reason='fixture',
                status='complete',
                command='orchestrate',
                command_kwargs={'steps': 'holders'},
                result_summary={
                    'total_succeeded': 0,
                    'total_failed': 0,
                    'total_skipped': 10,
                    'steps': {
                        'holders': {
                            'mode': 'per_coin',
                            'succeeded': 0,
                            'failed': 0,
                            'skipped': 10,
                            'records_loaded': 0,
                        },
                    },
                },
                repaired_state=True,
                snapshot_taken=False,
            )
        U001OpsSnapshot.objects.create(
            snapshot_date=dj_timezone.localdate(now),
            discovered_count=1,
            mapped_count=1,
            fl002_complete_count=0,
            rd001_complete_count=1,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            options={'rpc_source': 'helius_api_key_1'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001FL001DerivedAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            coin_count=1,
            candle_count=3,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        out = io.StringIO()
        call_command(
            'audit_u001',
            min_fl002_complete_pct=0.5,
            min_rd001_complete_pct=0.5,
            max_rd001_transport_statuses=1,
            max_single_lane_streak=4,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('latest_complete_action: holders_catchup', output)
        self.assertIn('latest_complete_streak: 4', output)
        self.assertIn('4 consecutive holders_catchup ticks with 0 loaded holder rows', output)
        self.assertIn('without loading any holder rows', output)

    def test_command_warns_when_pool_mapping_ticks_do_not_map_recent_coins(self):
        now = dj_timezone.now()
        recent_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_POOL_SPIN_RECENT',
            anchor_event=now - timedelta(hours=6),
        )
        U001AutomationState.objects.create(
            singleton_key='u001',
            last_tick_at=now - timedelta(minutes=10),
            last_action='pool_mapping_recent',
            last_action_status='complete',
            last_action_completed_at=now - timedelta(minutes=9),
            consecutive_failures=0,
        )
        for offset in range(4):
            U001AutomationTick.objects.create(
                started_at=now - timedelta(minutes=40 - offset),
                completed_at=now - timedelta(minutes=39 - offset),
                action='pool_mapping_recent',
                reason='fixture',
                status='complete',
                command='orchestrate',
                command_kwargs={'steps': 'pool_mapping'},
                result_summary={
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
        U001OpsSnapshot.objects.create(
            snapshot_date=dj_timezone.localdate(now),
            discovered_count=1,
            mapped_count=0,
            fl002_complete_count=0,
            rd001_complete_count=0,
        )
        U001SourceAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )
        U001RD001ChainAuditRun.objects.create(
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(minutes=5),
            status='ok',
            options={'rpc_source': 'helius_api_key_1'},
            coin_count=1,
            transaction_count=1,
            finding_count=0,
            warning_count=0,
            summary={'findings': [], 'warnings': []},
        )

        out = io.StringIO()
        call_command(
            'audit_u001',
            min_fl002_complete_pct=0.0,
            min_rd001_complete_pct=0.0,
            min_recent_mapped_pct=0.0,
            max_rd001_transport_statuses=1,
            max_single_lane_streak=4,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('latest_complete_action: pool_mapping_recent', output)
        self.assertIn('latest_complete_streak: 4', output)
        self.assertIn('4 consecutive pool_mapping_recent ticks with 0 total mapped coins', output)
        self.assertIn('without mapping any recent coins', output)


class AuditU001SourcesCommandTest(TestCase):
    @patch('warehouse.management.commands.audit_u001_sources.fetch_graduated_tokens')
    def test_command_detects_discovery_lag(self, mock_fetch_graduated):
        coin = MigratedCoin.objects.create(
            mint_address='AUDIT_SRC_DISC',
            anchor_event=T0,
        )
        old_time = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
        MigratedCoin.objects.filter(pk=coin.pk).update(ingested_at=old_time)
        mock_fetch_graduated.return_value = {
            'result': [
                {'graduatedAt': '2026-03-01T20:00:00.000Z'},
            ],
        }

        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command(
                'audit_u001_sources',
                sample_fl001=0,
                sample_fl002=0,
                sample_rd001=0,
                fail_on_findings=True,
                stdout=out,
            )

        output = out.getvalue()
        self.assertIn('U-001 LIVE SOURCE AUDIT', output)
        self.assertIn('Discovery lag is', output)

    @patch('warehouse.management.commands.audit_u001_sources.fetch_ohlcv')
    @patch('warehouse.management.commands.audit_u001_sources.fetch_graduated_tokens')
    def test_command_reports_fl001_sample_match(self, mock_fetch_graduated, mock_fetch_ohlcv):
        now = dj_timezone.now()
        coin = MigratedCoin.objects.create(
            mint_address='AUDIT_SRC_FL001',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL_AUDIT_SRC_FL001',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='FL-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )

        end = coin.window_end_time
        ts1 = (end - timedelta(minutes=10)).replace(second=0, microsecond=0)
        ts2 = (end - timedelta(minutes=5)).replace(second=0, microsecond=0)
        for ts in (ts1, ts2):
            OHLCVCandle.objects.create(
                coin=coin,
                timestamp=ts,
                open_price=1,
                high_price=1,
                low_price=1,
                close_price=1,
                volume=1,
            )

        mock_fetch_graduated.return_value = {
            'result': [
                {'graduatedAt': coin.anchor_event.isoformat().replace('+00:00', '.000Z')},
            ],
        }
        mock_fetch_ohlcv.return_value = (
            [
                [int(ts1.timestamp()), '1', '1', '1', '1', '1'],
                [int(ts2.timestamp()), '1', '1', '1', '1', '1'],
            ],
            {'api_calls': 1},
        )

        out = io.StringIO()
        call_command(
            'audit_u001_sources',
            sample_fl001=1,
            sample_fl002=0,
            sample_rd001=0,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('[ok] FL-001 sample matched', output)
        run = U001SourceAuditRun.objects.get()
        self.assertEqual(run.status, 'ok')
        self.assertEqual(run.finding_count, 0)
        self.assertEqual(run.warning_count, 0)

    @patch('warehouse.management.commands.audit_u001_sources.fetch_holders')
    @patch('warehouse.management.commands.audit_u001_sources.fetch_graduated_tokens')
    def test_command_aligns_fl002_audit_window_to_avoid_boundary_false_positive(
        self,
        mock_fetch_graduated,
        mock_fetch_holders,
    ):
        anchor = datetime(2026, 2, 26, 4, 20, 41, tzinfo=timezone.utc)
        coin = MigratedCoin.objects.create(
            mint_address='AUDIT_SRC_FL002_ALIGN',
            anchor_event=anchor,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='FL-002',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )

        end = coin.window_end_time
        ts1 = datetime(2026, 3, 1, 14, 40, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 3, 1, 14, 45, 0, tzinfo=timezone.utc)
        for ts in (ts1, ts2):
            HolderSnapshot.objects.create(
                coin=coin,
                timestamp=ts,
                total_holders=100,
                net_holder_change=0,
            )

        mock_fetch_graduated.return_value = {
            'result': [
                {'graduatedAt': coin.anchor_event.isoformat().replace('+00:00', '.000Z')},
            ],
        }
        mock_fetch_holders.return_value = (
            [
                {'timestamp': '2026-03-01T14:40:00.000Z', 'totalHolders': 100, 'netHolderChange': 0},
                {'timestamp': '2026-03-01T14:45:00.000Z', 'totalHolders': 100, 'netHolderChange': 0},
            ],
            {'api_calls': 1, 'cu_consumed': 50},
        )

        out = io.StringIO()
        call_command(
            'audit_u001_sources',
            sample_fl001=0,
            sample_fl002=1,
            sample_rd001=0,
            fl002_hours=1,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('[ok] FL-002 sample matched', output)
        self.assertNotIn('FL-002 mismatch', output)

    @patch('warehouse.management.commands.audit_u001_sources.fetch_ohlcv')
    @patch('warehouse.management.commands.audit_u001_sources.fetch_graduated_tokens')
    def test_command_skips_empty_fl001_sample_windows_before_informative_match(
        self,
        mock_fetch_graduated,
        mock_fetch_ohlcv,
    ):
        now = dj_timezone.now()
        empty_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_SRC_EMPTY_FL001',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=1),
        )
        informative_coin = MigratedCoin.objects.create(
            mint_address='AUDIT_SRC_INFO_FL001',
            anchor_event=now - MigratedCoin.OBSERVATION_WINDOW_END - timedelta(days=2),
        )
        for coin, pool in (
            (empty_coin, 'POOL_AUDIT_SRC_EMPTY_FL001'),
            (informative_coin, 'POOL_AUDIT_SRC_INFO_FL001'),
        ):
            PoolMapping.objects.create(
                coin=coin,
                pool_address=pool,
                dex='pumpswap',
                source='fixture',
            )
            U001PipelineStatus.objects.create(
                coin=coin,
                layer_id='FL-001',
                status=PipelineCompleteness.WINDOW_COMPLETE,
            )

        end = informative_coin.window_end_time
        ts = (end - timedelta(minutes=5)).replace(second=0, microsecond=0)
        OHLCVCandle.objects.create(
            coin=informative_coin,
            timestamp=ts,
            open_price=1,
            high_price=1,
            low_price=1,
            close_price=1,
            volume=1,
        )

        mock_fetch_graduated.return_value = {
            'result': [
                {'graduatedAt': empty_coin.anchor_event.isoformat().replace('+00:00', '.000Z')},
            ],
        }
        mock_fetch_ohlcv.side_effect = [
            ([], {'api_calls': 1}),
            ([[int(ts.timestamp()), '1', '1', '1', '1', '1']], {'api_calls': 1}),
        ]

        out = io.StringIO()
        call_command(
            'audit_u001_sources',
            sample_fl001=1,
            sample_fl002=0,
            sample_rd001=0,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('[ok] FL-001 sample matched for AUDIT_SRC_INFO_FL001', output)
        self.assertIn('[info] FL-001 skipped 1 empty sample windows', output)
        run = U001SourceAuditRun.objects.get()
        self.assertEqual(run.status, 'ok')
        self.assertEqual(run.warning_count, 0)
        self.assertEqual(
            run.summary['layers']['fl001'][1]['detail'],
            'FL-001 skipped 1 empty sample windows before selecting 1 informative sample(s).',
        )

    @patch('warehouse.management.commands.audit_u001_sources.fetch_graduated_tokens')
    def test_command_reports_rd001_candidate_diagnostics_when_none_available(
        self,
        mock_fetch_graduated,
    ):
        now = dj_timezone.now()
        coin = MigratedCoin.objects.create(
            mint_address='AUDIT_SRC_NO_RD001',
            anchor_event=now - timedelta(hours=6),
        )
        mock_fetch_graduated.return_value = {
            'result': [
                {'graduatedAt': coin.anchor_event.isoformat().replace('+00:00', '.000Z')},
            ],
        }

        out = io.StringIO()
        call_command(
            'audit_u001_sources',
            sample_fl001=0,
            sample_fl002=0,
            sample_rd001=1,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn(
            'No recent RD-001 Shyft sample candidates were available (recent_discovered=1, recent_mapped=0, recent_with_raw=0, recent_with_rd001_status=0, recent_partial_or_complete=0).',
            output,
        )


class AuditU001RD001ChainCommandTest(TestCase):
    @patch('warehouse.management.commands.audit_u001_rd001_chain.fetch_signatures_for_address')
    @patch('warehouse.management.commands.audit_u001_rd001_chain.fetch_transaction')
    def test_command_persists_direct_rpc_match_run(self, mock_fetch_transaction, mock_fetch_signatures):
        coin = MigratedCoin.objects.create(
            mint_address='CHAIN_AUDIT_COIN',
            anchor_event=T0,
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='CHAIN_AUDIT_POOL',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=T0,
            tx_signature='CHAIN_SIG_1',
            trade_type='BUY',
            wallet_address='CHAIN_WALLET',
            token_amount=300,
            sol_amount=50,
            pool_address='CHAIN_AUDIT_POOL',
            tx_fee='0.000005',
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=1,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status=PipelineCompleteness.PARTIAL,
        )
        mock_fetch_signatures.return_value = [
            {'signature': 'CHAIN_SIG_1', 'blockTime': int(T0.timestamp()), 'err': None},
        ]
        mock_fetch_transaction.return_value = {
            'blockTime': int(T0.timestamp()),
            'meta': {
                'err': None,
                'fee': 5000,
                'preTokenBalances': [
                    {
                        'owner': 'CHAIN_AUDIT_POOL',
                        'mint': 'CHAIN_AUDIT_COIN',
                        'uiTokenAmount': {'amount': '1000'},
                    },
                ],
                'postTokenBalances': [
                    {
                        'owner': 'CHAIN_AUDIT_POOL',
                        'mint': 'CHAIN_AUDIT_COIN',
                        'uiTokenAmount': {'amount': '700'},
                    },
                ],
            },
            'transaction': {
                'message': {
                    'accountKeys': [
                        {'pubkey': 'CHAIN_WALLET', 'signer': True},
                    ],
                },
            },
        }

        out = io.StringIO()
        call_command(
            'audit_u001_rd001_chain',
            sample_coins=1,
            txs_per_coin=1,
            hours=1,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('[ok] Direct-RPC match for CHAIN_AUDIT_COIN CHAIN_SIG_1', output)
        run = U001RD001ChainAuditRun.objects.get()
        self.assertEqual(run.status, 'ok')
        self.assertEqual(run.coin_count, 1)
        self.assertEqual(run.transaction_count, 1)
        self.assertEqual(run.finding_count, 0)
        self.assertEqual(run.warning_count, 0)
        self.assertEqual(run.summary['aggregate']['statuses'], {'ok': 1})
        self.assertEqual(run.summary['window_aggregate']['statuses'], {'ok': 1})

    @patch('warehouse.management.commands.audit_u001_rd001_chain.fetch_signatures_for_address')
    @patch('warehouse.management.commands.audit_u001_rd001_chain.fetch_transaction')
    def test_command_persists_finding_on_direct_rpc_mismatch(self, mock_fetch_transaction, mock_fetch_signatures):
        coin = MigratedCoin.objects.create(
            mint_address='CHAIN_AUDIT_FINDING',
            anchor_event=T0,
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='CHAIN_AUDIT_POOL_FINDING',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=T0,
            tx_signature='CHAIN_SIG_FINDING',
            trade_type='BUY',
            wallet_address='CHAIN_WALLET_FINDING',
            token_amount=300,
            sol_amount=50,
            pool_address='CHAIN_AUDIT_POOL_FINDING',
            tx_fee='0.000005',
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=1,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status=PipelineCompleteness.PARTIAL,
        )
        mock_fetch_signatures.return_value = []
        mock_fetch_transaction.return_value = None

        out = io.StringIO()
        call_command(
            'audit_u001_rd001_chain',
            sample_coins=1,
            txs_per_coin=1,
            hours=1,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('[finding] Direct-RPC mismatch for CHAIN_AUDIT_FINDING CHAIN_SIG_FINDING', output)
        run = U001RD001ChainAuditRun.objects.get(started_at__isnull=False, status='finding')
        self.assertEqual(run.finding_count, 2)
        self.assertEqual(run.summary['aggregate']['finding_buckets'], {'missing_on_chain': 1})
        self.assertEqual(
            run.summary['window_aggregate']['finding_buckets'],
            {'extra_trade_signatures': 1},
        )

    @patch('warehouse.management.commands.audit_u001_rd001_chain.fetch_signatures_for_address')
    @patch('warehouse.management.commands.audit_u001_rd001_chain.fetch_transaction')
    def test_command_warns_on_ambiguous_window_signatures(self, mock_fetch_transaction, mock_fetch_signatures):
        coin = MigratedCoin.objects.create(
            mint_address='CHAIN_AUDIT_WARN',
            anchor_event=T0,
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='CHAIN_AUDIT_WARN_POOL',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=T0,
            tx_signature='CHAIN_WARN_SIG_1',
            trade_type='BUY',
            wallet_address='CHAIN_WALLET_WARN',
            token_amount=300,
            sol_amount=50,
            pool_address='CHAIN_AUDIT_WARN_POOL',
            tx_fee='0.000005',
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=1,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status=PipelineCompleteness.PARTIAL,
        )
        mock_fetch_signatures.return_value = [
            {'signature': 'CHAIN_WARN_SIG_1', 'blockTime': int(T0.timestamp()), 'err': None},
            {'signature': 'CHAIN_WARN_SIG_2', 'blockTime': int(T0.timestamp()), 'err': None},
        ]

        def _mock_fetch(signature, rpc_url=None):
            if signature == 'CHAIN_WARN_SIG_1':
                return {
                    'blockTime': int(T0.timestamp()),
                    'meta': {
                        'err': None,
                        'fee': 5000,
                        'preTokenBalances': [
                            {
                                'owner': 'CHAIN_AUDIT_WARN_POOL',
                                'mint': 'CHAIN_AUDIT_WARN',
                                'uiTokenAmount': {'amount': '1000'},
                            },
                        ],
                        'postTokenBalances': [
                            {
                                'owner': 'CHAIN_AUDIT_WARN_POOL',
                                'mint': 'CHAIN_AUDIT_WARN',
                                'uiTokenAmount': {'amount': '700'},
                            },
                        ],
                    },
                    'transaction': {
                        'message': {
                            'accountKeys': [{'pubkey': 'CHAIN_WALLET_WARN', 'signer': True}],
                        },
                    },
                }
            return {
                'blockTime': int(T0.timestamp()),
                'meta': {
                    'err': None,
                    'fee': 5000,
                    'preTokenBalances': [],
                    'postTokenBalances': [],
                },
                'transaction': {
                    'message': {
                        'accountKeys': [{'pubkey': 'CHAIN_WALLET_WARN', 'signer': True}],
                    },
                },
            }

        mock_fetch_transaction.side_effect = _mock_fetch

        out = io.StringIO()
        call_command(
            'audit_u001_rd001_chain',
            sample_coins=1,
            txs_per_coin=1,
            hours=1,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('[warning] Direct-RPC window partial match for CHAIN_AUDIT_WARN', output)
        run = U001RD001ChainAuditRun.objects.filter(status='warning').latest('started_at')
        self.assertEqual(run.warning_count, 1)
        self.assertEqual(
            run.summary['window_aggregate']['warning_buckets'],
            {'ambiguous_pool_signatures': 1},
        )

    @patch('warehouse.management.commands.audit_u001_rd001_chain.fetch_signatures_for_address')
    @patch('warehouse.management.commands.audit_u001_rd001_chain.fetch_transaction')
    def test_command_warns_when_window_scan_fails(self, mock_fetch_transaction, mock_fetch_signatures):
        coin = MigratedCoin.objects.create(
            mint_address='CHAIN_AUDIT_WINDOW_FAIL',
            anchor_event=T0,
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='CHAIN_AUDIT_WINDOW_FAIL_POOL',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=T0,
            tx_signature='CHAIN_FAIL_SIG_1',
            trade_type='BUY',
            wallet_address='CHAIN_WALLET_FAIL',
            token_amount=300,
            sol_amount=50,
            pool_address='CHAIN_AUDIT_WINDOW_FAIL_POOL',
            tx_fee='0.000005',
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=1,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status=PipelineCompleteness.PARTIAL,
        )
        mock_fetch_transaction.return_value = {
            'blockTime': int(T0.timestamp()),
            'meta': {
                'err': None,
                'fee': 5000,
                'preTokenBalances': [
                    {
                        'owner': 'CHAIN_AUDIT_WINDOW_FAIL_POOL',
                        'mint': 'CHAIN_AUDIT_WINDOW_FAIL',
                        'uiTokenAmount': {'amount': '1000'},
                    },
                ],
                'postTokenBalances': [
                    {
                        'owner': 'CHAIN_AUDIT_WINDOW_FAIL_POOL',
                        'mint': 'CHAIN_AUDIT_WINDOW_FAIL',
                        'uiTokenAmount': {'amount': '700'},
                    },
                ],
            },
            'transaction': {
                'message': {
                    'accountKeys': [{'pubkey': 'CHAIN_WALLET_FAIL', 'signer': True}],
                },
            },
        }
        mock_fetch_signatures.side_effect = RuntimeError('rate_limited_429')

        out = io.StringIO()
        call_command(
            'audit_u001_rd001_chain',
            sample_coins=1,
            txs_per_coin=1,
            hours=1,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('[warning] Direct-RPC window scan could not complete for CHAIN_AUDIT_WINDOW_FAIL', output)
        run = U001RD001ChainAuditRun.objects.filter(status='warning').latest('started_at')
        self.assertEqual(run.finding_count, 0)
        self.assertEqual(run.warning_count, 1)
        self.assertEqual(
            run.summary['window_aggregate']['warning_buckets'],
            {'window_scan_failed': 1},
        )

    @patch('warehouse.management.commands.audit_u001_rd001_chain.fetch_signatures_for_address')
    @patch('warehouse.management.commands.audit_u001_rd001_chain.fetch_transaction')
    def test_window_reconciliation_uses_full_warehouse_window_not_sample_subset(
        self,
        mock_fetch_transaction,
        mock_fetch_signatures,
    ):
        coin = MigratedCoin.objects.create(
            mint_address='CHAIN_AUDIT_FULL_WINDOW',
            anchor_event=T0,
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='CHAIN_AUDIT_FULL_WINDOW_POOL',
            dex='pumpswap',
            source='fixture',
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=T0,
            tx_signature='CHAIN_FULL_SIG_1',
            trade_type='BUY',
            wallet_address='CHAIN_WALLET_FULL',
            token_amount=300,
            sol_amount=50,
            pool_address='CHAIN_AUDIT_FULL_WINDOW_POOL',
            tx_fee='0.000005',
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=1,
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=T0 - timedelta(minutes=10),
            tx_signature='CHAIN_FULL_SIG_2',
            trade_type='BUY',
            wallet_address='CHAIN_WALLET_FULL',
            token_amount=200,
            sol_amount=40,
            pool_address='CHAIN_AUDIT_FULL_WINDOW_POOL',
            tx_fee='0.000005',
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=1,
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='RD-001',
            status=PipelineCompleteness.PARTIAL,
        )
        mock_fetch_signatures.return_value = [
            {'signature': 'CHAIN_FULL_SIG_1', 'blockTime': int(T0.timestamp()), 'err': None},
            {'signature': 'CHAIN_FULL_SIG_2', 'blockTime': int((T0 - timedelta(minutes=10)).timestamp()), 'err': None},
        ]

        def _tx(signature, rpc_url=None):
            if signature == 'CHAIN_FULL_SIG_1':
                amount = ('1000', '700')
                block_time = T0
            else:
                amount = ('900', '700')
                block_time = T0 - timedelta(minutes=10)
            return {
                'blockTime': int(block_time.timestamp()),
                'meta': {
                    'err': None,
                    'fee': 5000,
                    'preTokenBalances': [
                        {
                            'owner': 'CHAIN_AUDIT_FULL_WINDOW_POOL',
                            'mint': 'CHAIN_AUDIT_FULL_WINDOW',
                            'uiTokenAmount': {'amount': amount[0]},
                        },
                    ],
                    'postTokenBalances': [
                        {
                            'owner': 'CHAIN_AUDIT_FULL_WINDOW_POOL',
                            'mint': 'CHAIN_AUDIT_FULL_WINDOW',
                            'uiTokenAmount': {'amount': amount[1]},
                        },
                    ],
                },
                'transaction': {
                    'message': {
                        'accountKeys': [{'pubkey': 'CHAIN_WALLET_FULL', 'signer': True}],
                    },
                },
            }

        mock_fetch_transaction.side_effect = _tx

        out = io.StringIO()
        call_command(
            'audit_u001_rd001_chain',
            sample_coins=1,
            txs_per_coin=1,
            hours=1,
            stdout=out,
        )

        run = U001RD001ChainAuditRun.objects.filter(status='ok').latest('started_at')
        self.assertEqual(run.summary['window_aggregate']['statuses'], {'ok': 1})


class AuditU001FL001DerivedCommandTest(TestCase):
    def test_command_persists_ok_run_for_matching_derived_candle(self):
        coin = MigratedCoin.objects.create(
            mint_address='FL001_DERIVED_OK',
            anchor_event=T0 - timedelta(hours=1),
            decimals=6,
        )
        PoolMapping.objects.create(
            coin=coin,
            pool_address='POOL_FL001_DERIVED_OK',
            dex='pumpswap',
            source='fixture',
        )
        U001PipelineStatus.objects.create(
            coin=coin,
            layer_id='FL-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        RawTransaction.objects.create(
            coin=coin,
            timestamp=T0,
            tx_signature='FL001_DERIVED_SIG_1',
            trade_type='BUY',
            wallet_address='WALLET_FL001_DERIVED',
            token_amount=1_000_000,
            sol_amount=100_000_000,
            pool_address='POOL_FL001_DERIVED_OK',
            tx_fee='0.001',
            lp_fee=1,
            protocol_fee=1,
            coin_creator_fee=1,
        )
        OHLCVCandle.objects.create(
            coin=coin,
            timestamp=T0,
            open_price=Decimal('12'),
            high_price=Decimal('12'),
            low_price=Decimal('12'),
            close_price=Decimal('12'),
            volume=Decimal('12'),
        )
        sol = BinanceAsset.objects.create(
            symbol='SOLUSDT',
            base_asset='SOL',
            quote_asset='USDT',
            anchor_event=T0 - timedelta(days=1),
        )
        U002OHLCVCandle.objects.create(
            asset=sol,
            timestamp=T0,
            open_price=Decimal('120'),
            high_price=Decimal('120'),
            low_price=Decimal('120'),
            close_price=Decimal('120'),
            volume=Decimal('1'),
            quote_volume=Decimal('120'),
            trade_count=1,
            taker_buy_volume=Decimal('1'),
            taker_buy_quote_volume=Decimal('120'),
        )

        out = io.StringIO()
        call_command(
            'audit_u001_fl001_derived',
            sample_coins=1,
            hours=1,
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn('[ok] Derived FL-001 matched for FL001_DERIVED_OK', output)
        run = U001FL001DerivedAuditRun.objects.get()
        self.assertEqual(run.status, 'ok')
        self.assertEqual(run.coin_count, 1)
        self.assertEqual(run.candle_count, 1)
        self.assertEqual(run.summary['aggregate']['statuses'], {'ok': 1})
