"""Tests for cross-layer alignment (inner join)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from data_service.operations import get_panel_slice
from warehouse.models import (
    HolderSnapshot,
    MigratedCoin,
    OHLCVCandle,
)

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


class AlignmentInnerJoinTest(TestCase):
    """FL-001 has candles at 10:00, 10:05, 10:10.
    FL-002 has snapshots at 10:00, 10:05 (but NOT 10:10).

    Inner join should return only 2 rows (10:00, 10:05).
    """

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='ALIGN_TEST', anchor_event=T0,
        )

        # FL-001: 3 candles
        for offset in [0, 5, 10]:
            OHLCVCandle.objects.create(
                coin=self.coin,
                timestamp=T0 + timedelta(minutes=offset),
                open_price=Decimal('10'),
                high_price=Decimal('12'),
                low_price=Decimal('9'),
                close_price=Decimal('11'),
                volume=Decimal('100'),
            )

        # FL-002: 2 snapshots (no 10:10)
        for offset in [0, 5]:
            HolderSnapshot.objects.create(
                coin=self.coin,
                timestamp=T0 + timedelta(minutes=offset),
                total_holders=1000,
                net_holder_change=5,
            )

    def test_inner_join_drops_unmatched(self):
        # At 10:15 all intervals are closed for PIT
        sim = T0 + timedelta(minutes=15)
        result = get_panel_slice(
            ['ALIGN_TEST'], ['FL-001', 'FL-002'], sim,
        )
        self.assertEqual(len(result), 2)

    def test_merged_rows_have_both_layers(self):
        sim = T0 + timedelta(minutes=15)
        result = get_panel_slice(
            ['ALIGN_TEST'], ['FL-001', 'FL-002'], sim,
        )
        for row in result:
            # FL-001 fields
            self.assertIn('open_price', row)
            self.assertIn('volume', row)
            # FL-002 fields
            self.assertIn('total_holders', row)
            self.assertIn('net_holder_change', row)

    def test_timestamps_are_correct(self):
        sim = T0 + timedelta(minutes=15)
        result = get_panel_slice(
            ['ALIGN_TEST'], ['FL-001', 'FL-002'], sim,
        )
        timestamps = sorted(r['timestamp'] for r in result)
        self.assertEqual(timestamps, [T0, T0 + timedelta(minutes=5)])
