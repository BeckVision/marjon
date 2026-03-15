"""Tests for warehouse models — constraints, membership_end, validation."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from warehouse.models import (
    MigratedCoin, OHLCVCandle, PipelineBatchRun, PipelineCompleteness,
    RawTransaction, RunMode, RunStatus, U001PipelineRun, U001PipelineStatus,
)

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


class OHLCVCheckConstraintTest(TestCase):
    """DQ-002/DQ-003: CHECK constraints on OHLCVCandle."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='CONSTRAINT_TEST', anchor_event=T0,
        )

    def test_high_gte_low_ok(self):
        """high >= low should succeed."""
        OHLCVCandle.objects.create(
            coin=self.coin,
            timestamp=T0,
            open_price=Decimal('10'),
            high_price=Decimal('12'),
            low_price=Decimal('9'),
            close_price=Decimal('11'),
            volume=Decimal('100'),
        )
        self.assertEqual(OHLCVCandle.objects.count(), 1)

    def test_high_lt_low_violates(self):
        """high < low should raise IntegrityError."""
        with self.assertRaises(IntegrityError):
            OHLCVCandle.objects.create(
                coin=self.coin,
                timestamp=T0,
                open_price=Decimal('10'),
                high_price=Decimal('8'),
                low_price=Decimal('9'),
                close_price=Decimal('10'),
                volume=Decimal('100'),
            )

    def test_negative_volume_violates(self):
        """Negative volume should raise IntegrityError."""
        with self.assertRaises(IntegrityError):
            OHLCVCandle.objects.create(
                coin=self.coin,
                timestamp=T0,
                open_price=Decimal('10'),
                high_price=Decimal('12'),
                low_price=Decimal('9'),
                close_price=Decimal('11'),
                volume=Decimal('-1'),
            )

    def test_open_outside_range_violates(self):
        """open_price outside [low, high] should raise IntegrityError."""
        with self.assertRaises(IntegrityError):
            OHLCVCandle.objects.create(
                coin=self.coin,
                timestamp=T0,
                open_price=Decimal('15'),
                high_price=Decimal('12'),
                low_price=Decimal('9'),
                close_price=Decimal('11'),
                volume=Decimal('100'),
            )

    def test_close_outside_range_violates(self):
        """close_price outside [low, high] should raise IntegrityError."""
        with self.assertRaises(IntegrityError):
            OHLCVCandle.objects.create(
                coin=self.coin,
                timestamp=T0,
                open_price=Decimal('10'),
                high_price=Decimal('12'),
                low_price=Decimal('9'),
                close_price=Decimal('5'),
                volume=Decimal('100'),
            )


class MembershipEndTest(TestCase):
    """Verify membership_end excludes coins from universe queries."""

    def setUp(self):
        self.active_coin = MigratedCoin.objects.create(
            mint_address='ACTIVE_COIN', anchor_event=T0,
        )
        self.ended_coin = MigratedCoin.objects.create(
            mint_address='ENDED_COIN',
            anchor_event=T0,
            membership_end=T0 + timedelta(hours=1),
        )

    def test_active_coin_in_universe(self):
        sim = T0 + timedelta(hours=2)
        members = MigratedCoin.objects.as_of(sim)
        mints = list(members.values_list('mint_address', flat=True))
        self.assertIn('ACTIVE_COIN', mints)

    def test_ended_coin_excluded_after_end(self):
        sim = T0 + timedelta(hours=2)
        members = MigratedCoin.objects.as_of(sim)
        mints = list(members.values_list('mint_address', flat=True))
        self.assertNotIn('ENDED_COIN', mints)

    def test_ended_coin_included_before_end(self):
        sim = T0 + timedelta(minutes=30)
        members = MigratedCoin.objects.as_of(sim)
        mints = list(members.values_list('mint_address', flat=True))
        self.assertIn('ENDED_COIN', mints)


class ReferenceDataHappyPathTest(TestCase):
    """Test get_reference_data returns correct rows within time range."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='REF_HAPPY', anchor_event=T0,
        )
        # Create 3 transactions at different times
        for i, offset in enumerate([10, 30, 60]):
            RawTransaction.objects.create(
                coin=self.coin,
                timestamp=T0 + timedelta(minutes=offset),
                tx_signature=f'SIG_REF_{i}',
                trade_type='BUY',
                wallet_address='WALLET_REF',
                token_amount=1000,
                sol_amount=500,
                pool_address='POOL_REF',
                tx_fee=Decimal('0.001'),
                lp_fee=10,
                protocol_fee=5,
                coin_creator_fee=3,
                pool_token_reserves=100000,
                pool_sol_reserves=50000,
            )

    def test_returns_all_in_range(self):
        from data_service.operations import get_reference_data
        result = get_reference_data(
            'REF_HAPPY',
            T0,
            T0 + timedelta(hours=2),
            T0 + timedelta(hours=2),
        )
        self.assertEqual(result.count(), 3)

    def test_filters_by_time_range(self):
        from data_service.operations import get_reference_data
        result = get_reference_data(
            'REF_HAPPY',
            T0 + timedelta(minutes=20),
            T0 + timedelta(minutes=50),
            T0 + timedelta(hours=2),
        )
        self.assertEqual(result.count(), 1)
        self.assertEqual(
            result.first().timestamp,
            T0 + timedelta(minutes=30),
        )

    def test_pit_enforcement(self):
        """sim_time before a transaction should exclude it."""
        from data_service.operations import get_reference_data
        # sim_time at T0+20 min — only the T0+10 transaction is visible
        result = get_reference_data(
            'REF_HAPPY',
            T0,
            T0 + timedelta(hours=2),
            T0 + timedelta(minutes=20),
        )
        self.assertEqual(result.count(), 1)


class PipelineBatchRunTest(TestCase):
    """Tests for PipelineBatchRun operational model."""

    def test_create_batch(self):
        batch = PipelineBatchRun.objects.create(
            pipeline_id='fl001',
            mode=RunMode.BOOTSTRAP,
            status=RunStatus.STARTED,
            started_at=T0,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.pipeline_id, 'fl001')
        self.assertEqual(batch.mode, 'bootstrap')
        self.assertEqual(batch.status, 'started')
        self.assertEqual(batch.started_at, T0)
        self.assertIsNone(batch.completed_at)
        self.assertEqual(batch.coins_attempted, 0)

    def test_update_batch_to_complete(self):
        batch = PipelineBatchRun.objects.create(
            pipeline_id='fl002',
            mode=RunMode.STEADY_STATE,
            status=RunStatus.STARTED,
            started_at=T0,
        )
        done = T0 + timedelta(minutes=5)
        batch.status = RunStatus.COMPLETE
        batch.completed_at = done
        batch.coins_succeeded = 42
        batch.save()

        batch.refresh_from_db()
        self.assertEqual(batch.status, 'complete')
        self.assertEqual(batch.completed_at, done)
        self.assertEqual(batch.coins_succeeded, 42)

    def test_invalid_status_rejected(self):
        batch = PipelineBatchRun(
            pipeline_id='fl001',
            mode=RunMode.BOOTSTRAP,
            status='invalid_status',
            started_at=T0,
        )
        with self.assertRaises(ValidationError):
            batch.full_clean()


class U001PipelineRunTest(TestCase):
    """Tests for U001PipelineRun operational model."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='RUN_TEST', anchor_event=T0,
        )

    def test_create_run_linked_to_batch(self):
        batch = PipelineBatchRun.objects.create(
            pipeline_id='fl001',
            mode=RunMode.BOOTSTRAP,
            status=RunStatus.STARTED,
            started_at=T0,
        )
        run = U001PipelineRun.objects.create(
            batch=batch,
            coin=self.coin,
            layer_id='FL-001',
            mode=RunMode.BOOTSTRAP,
            status=RunStatus.COMPLETE,
            started_at=T0,
            records_loaded=100,
        )
        self.assertEqual(batch.u001pipelinerun_runs.count(), 1)
        self.assertEqual(batch.u001pipelinerun_runs.first().pk, run.pk)

    def test_create_run_without_batch(self):
        run = U001PipelineRun.objects.create(
            batch=None,
            coin=self.coin,
            layer_id='FL-001',
            mode=RunMode.REFILL,
            status=RunStatus.COMPLETE,
            started_at=T0,
        )
        run.refresh_from_db()
        self.assertIsNone(run.batch)

    def test_multiple_runs_per_coin(self):
        for i in range(3):
            U001PipelineRun.objects.create(
                coin=self.coin,
                layer_id='FL-001',
                mode=RunMode.STEADY_STATE,
                status=RunStatus.ERROR if i < 2 else RunStatus.COMPLETE,
                started_at=T0 + timedelta(minutes=i * 10),
            )
        runs = U001PipelineRun.objects.filter(
            coin=self.coin, layer_id='FL-001',
        ).order_by('-started_at')
        self.assertEqual(runs.count(), 3)
        self.assertEqual(runs.first().started_at, T0 + timedelta(minutes=20))

    def test_query_latest_status_per_coin(self):
        for i, status in enumerate([RunStatus.ERROR, RunStatus.ERROR, RunStatus.COMPLETE]):
            U001PipelineRun.objects.create(
                coin=self.coin,
                layer_id='FL-001',
                mode=RunMode.STEADY_STATE,
                status=status,
                started_at=T0 + timedelta(minutes=i * 10),
            )
        latest = U001PipelineRun.objects.filter(
            coin=self.coin, layer_id='FL-001',
        ).order_by('-started_at').first()
        self.assertEqual(latest.status, RunStatus.COMPLETE)

    def test_query_all_failures(self):
        U001PipelineRun.objects.create(
            coin=self.coin, layer_id='FL-001',
            mode=RunMode.STEADY_STATE, status=RunStatus.COMPLETE,
            started_at=T0,
        )
        U001PipelineRun.objects.create(
            coin=self.coin, layer_id='FL-001',
            mode=RunMode.STEADY_STATE, status=RunStatus.ERROR,
            started_at=T0 + timedelta(minutes=10),
        )
        U001PipelineRun.objects.create(
            coin=self.coin, layer_id='FL-002',
            mode=RunMode.BOOTSTRAP, status=RunStatus.ERROR,
            started_at=T0 + timedelta(minutes=20),
        )
        errors = U001PipelineRun.objects.filter(status=RunStatus.ERROR)
        self.assertEqual(errors.count(), 2)


class U001PipelineStatusTest(TestCase):
    """Tests for U001PipelineStatus cache model."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='STATUS_TEST', anchor_event=T0,
        )

    def test_create_status(self):
        status = U001PipelineStatus.objects.create(
            coin=self.coin, layer_id='FL-001',
            status=PipelineCompleteness.PARTIAL,
        )
        status.refresh_from_db()
        self.assertEqual(status.coin_id, 'STATUS_TEST')
        self.assertEqual(status.layer_id, 'FL-001')
        self.assertEqual(status.status, 'partial')

    def test_unique_constraint(self):
        U001PipelineStatus.objects.create(
            coin=self.coin, layer_id='FL-001',
            status=PipelineCompleteness.PARTIAL,
        )
        with self.assertRaises(IntegrityError):
            U001PipelineStatus.objects.create(
                coin=self.coin, layer_id='FL-001',
                status=PipelineCompleteness.WINDOW_COMPLETE,
            )

    def test_update_in_place(self):
        U001PipelineStatus.objects.create(
            coin=self.coin, layer_id='FL-001',
            status=PipelineCompleteness.PARTIAL,
        )
        U001PipelineStatus.objects.update_or_create(
            coin=self.coin, layer_id='FL-001',
            defaults={'status': PipelineCompleteness.WINDOW_COMPLETE},
        )
        self.assertEqual(U001PipelineStatus.objects.count(), 1)
        self.assertEqual(
            U001PipelineStatus.objects.first().status,
            PipelineCompleteness.WINDOW_COMPLETE,
        )

    def test_query_by_status(self):
        coins = []
        for i in range(5):
            c = MigratedCoin.objects.create(
                mint_address=f'STATUS_Q_{i}', anchor_event=T0,
            )
            coins.append(c)
        statuses = [
            PipelineCompleteness.WINDOW_COMPLETE,
            PipelineCompleteness.WINDOW_COMPLETE,
            PipelineCompleteness.PARTIAL,
            PipelineCompleteness.PARTIAL,
            PipelineCompleteness.ERROR,
        ]
        for c, s in zip(coins, statuses):
            U001PipelineStatus.objects.create(
                coin=c, layer_id='FL-001', status=s,
            )
        partial = U001PipelineStatus.objects.filter(
            status=PipelineCompleteness.PARTIAL,
        )
        self.assertEqual(partial.count(), 2)

    def test_find_coins_without_status(self):
        coins = []
        for i in range(10):
            c = MigratedCoin.objects.create(
                mint_address=f'NO_STATUS_{i}', anchor_event=T0,
            )
            coins.append(c)
        # Create status for first 7 only
        for c in coins[:7]:
            U001PipelineStatus.objects.create(
                coin=c, layer_id='FL-001',
                status=PipelineCompleteness.PARTIAL,
            )
        # Exclude the setUp coin from the query
        without = MigratedCoin.objects.filter(
            mint_address__startswith='NO_STATUS_',
        ).exclude(
            pipeline_statuses__layer_id='FL-001',
        )
        self.assertEqual(without.count(), 3)


class MigratedCoinMaturityTest(TestCase):
    """Tests for is_mature and window_end_time properties."""

    def test_is_mature_true(self):
        # anchor_event 10 days ago — well past 5000 min (~3.47 days)
        coin = MigratedCoin(
            mint_address='MATURE_YES',
            anchor_event=T0 - timedelta(days=10),
        )
        self.assertTrue(coin.is_mature)

    def test_is_mature_false(self):
        from django.utils import timezone as dj_tz
        # anchor_event 1 hour ago — nowhere near 5000 min
        coin = MigratedCoin(
            mint_address='MATURE_NO',
            anchor_event=dj_tz.now() - timedelta(hours=1),
        )
        self.assertFalse(coin.is_mature)

    def test_window_end_time(self):
        coin = MigratedCoin(
            mint_address='WINDOW_END',
            anchor_event=T0,
        )
        expected = T0 + MigratedCoin.OBSERVATION_WINDOW_END
        self.assertEqual(coin.window_end_time, expected)
