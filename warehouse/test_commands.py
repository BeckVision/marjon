"""Tests for warehouse management commands."""

import io
from datetime import datetime, timedelta, timezone

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from warehouse.models import (
    HolderSnapshot,
    MigratedCoin,
    OHLCVCandle,
    PipelineBatchRun,
    PipelineCompleteness,
    RawTransaction,
    RunMode,
    RunStatus,
    U001PipelineRun,
    U001PipelineStatus,
)

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


class U001IngestionHealthCommandTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='HEALTH_CMD',
            anchor_event=T0,
        )
        old_time = dj_timezone.now() - timedelta(days=10)
        MigratedCoin.objects.filter(pk=self.coin.pk).update(ingested_at=old_time)

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
