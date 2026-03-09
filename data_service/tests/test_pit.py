"""Tests for PIT (point-in-time) enforcement in the data service."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from data_service.operations import get_panel_slice
from warehouse.models import MigratedCoin, OHLCVCandle

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


class PITEnforcementTest(TestCase):
    """The single most important test.

    Candles at 10:00, 10:05, 10:10.
    Interval-start convention (WDP9): interval_end = timestamp + 5min.

    At simulation_time=10:08:
    - 10:00 candle: interval 10:00-10:05, closed at 10:05 <= 10:08 -> VISIBLE
    - 10:05 candle: interval 10:05-10:10, closed at 10:10 > 10:08  -> NOT VISIBLE
    - 10:10 candle: interval 10:10-10:15, closed at 10:15 > 10:08  -> NOT VISIBLE
    """

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='PIT_TEST', anchor_event=T0,
        )
        for i, offset in enumerate([0, 5, 10]):
            OHLCVCandle.objects.create(
                coin=self.coin,
                timestamp=T0 + timedelta(minutes=offset),
                open_price=Decimal('10'),
                high_price=Decimal('12'),
                low_price=Decimal('9'),
                close_price=Decimal('11'),
                volume=Decimal('100'),
            )

    def test_at_10_08_only_first_candle_visible(self):
        sim = datetime(2026, 3, 1, 10, 8, tzinfo=timezone.utc)
        result = get_panel_slice(['PIT_TEST'], ['FL-001'], sim)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['timestamp'], T0)

    def test_at_10_10_two_candles_visible(self):
        sim = datetime(2026, 3, 1, 10, 10, tzinfo=timezone.utc)
        result = get_panel_slice(['PIT_TEST'], ['FL-001'], sim)
        self.assertEqual(len(result), 2)
        timestamps = [r['timestamp'] for r in result]
        self.assertIn(T0, timestamps)
        self.assertIn(T0 + timedelta(minutes=5), timestamps)

    def test_at_10_15_all_three_visible(self):
        sim = datetime(2026, 3, 1, 10, 15, tzinfo=timezone.utc)
        result = get_panel_slice(['PIT_TEST'], ['FL-001'], sim)
        self.assertEqual(len(result), 3)
