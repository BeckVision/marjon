"""Tests for warehouse models — constraints, membership_end, validation."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from warehouse.models import MigratedCoin, OHLCVCandle, RawTransaction

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
        for offset in [10, 30, 60]:
            RawTransaction.objects.create(
                coin=self.coin,
                timestamp=T0 + timedelta(minutes=offset),
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
