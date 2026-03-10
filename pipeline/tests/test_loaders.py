"""Tests for FL-001 and FL-002 loaders (delete-write pattern)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from pipeline.loaders.fl001 import get_watermark as fl001_watermark
from pipeline.loaders.fl001 import load as fl001_load
from pipeline.loaders.fl002 import get_watermark as fl002_watermark
from pipeline.loaders.fl002 import load as fl002_load
from warehouse.models import HolderSnapshot, MigratedCoin, OHLCVCandle

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


class FL001LoaderTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='LOADER_FL001', anchor_event=T0,
        )

    def _make_canonical(self, offsets):
        return [
            {
                'coin_id': 'LOADER_FL001',
                'timestamp': T0 + timedelta(minutes=m),
                'open_price': Decimal('10'),
                'high_price': Decimal('12'),
                'low_price': Decimal('9'),
                'close_price': Decimal('11'),
                'volume': Decimal('100'),
            }
            for m in offsets
        ]

    def test_load_inserts_records(self):
        canonical = self._make_canonical([0, 5, 10])
        end = T0 + timedelta(minutes=10)
        fl001_load('LOADER_FL001', T0, end, canonical)
        self.assertEqual(OHLCVCandle.objects.count(), 3)

    def test_load_delete_write_replaces(self):
        canonical = self._make_canonical([0, 5, 10])
        end = T0 + timedelta(minutes=10)
        fl001_load('LOADER_FL001', T0, end, canonical)

        # Re-load with different data in same range
        new_canonical = self._make_canonical([0, 5])
        fl001_load('LOADER_FL001', T0, end, new_canonical)
        self.assertEqual(OHLCVCandle.objects.count(), 2)

    def test_empty_canonical_raises_valueerror(self):
        end = T0 + timedelta(minutes=10)
        with self.assertRaises(ValueError):
            fl001_load('LOADER_FL001', T0, end, [])

    def test_watermark_none_when_no_data(self):
        self.assertIsNone(fl001_watermark('LOADER_FL001'))

    def test_watermark_returns_latest(self):
        canonical = self._make_canonical([0, 5, 10])
        end = T0 + timedelta(minutes=10)
        fl001_load('LOADER_FL001', T0, end, canonical)
        self.assertEqual(
            fl001_watermark('LOADER_FL001'),
            T0 + timedelta(minutes=10),
        )


class FL002LoaderTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='LOADER_FL002', anchor_event=T0,
        )

    def _make_canonical(self, offsets):
        return [
            {
                'coin_id': 'LOADER_FL002',
                'timestamp': T0 + timedelta(minutes=m),
                'total_holders': 1000,
                'net_holder_change': 5,
            }
            for m in offsets
        ]

    def test_load_inserts_records(self):
        canonical = self._make_canonical([0, 5, 10])
        end = T0 + timedelta(minutes=10)
        fl002_load('LOADER_FL002', T0, end, canonical)
        self.assertEqual(HolderSnapshot.objects.count(), 3)

    def test_load_delete_write_replaces(self):
        canonical = self._make_canonical([0, 5, 10])
        end = T0 + timedelta(minutes=10)
        fl002_load('LOADER_FL002', T0, end, canonical)

        new_canonical = self._make_canonical([0, 5])
        fl002_load('LOADER_FL002', T0, end, new_canonical)
        self.assertEqual(HolderSnapshot.objects.count(), 2)

    def test_empty_canonical_raises_valueerror(self):
        end = T0 + timedelta(minutes=10)
        with self.assertRaises(ValueError):
            fl002_load('LOADER_FL002', T0, end, [])

    def test_watermark_none_when_no_data(self):
        self.assertIsNone(fl002_watermark('LOADER_FL002'))

    def test_watermark_returns_latest(self):
        canonical = self._make_canonical([0, 5, 10])
        end = T0 + timedelta(minutes=10)
        fl002_load('LOADER_FL002', T0, end, canonical)
        self.assertEqual(
            fl002_watermark('LOADER_FL002'),
            T0 + timedelta(minutes=10),
        )
