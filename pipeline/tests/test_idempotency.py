"""Idempotency tests — verify that pipeline loaders are safe to re-run.

Delete-write for feature layers, upsert for universe/dimension tables.
Running a loader twice with identical data must produce the same row count.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from warehouse.models import MigratedCoin, OHLCVCandle, PoolMapping

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


class OHLCVLoaderIdempotencyTest(TestCase):
    """FL-001 loader uses delete-write — re-running must not create duplicates."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='IDEM_OHLCV', anchor_event=T0,
        )
        self.canonical = [
            {
                'coin_id': 'IDEM_OHLCV',
                'timestamp': T0 + timedelta(minutes=i * 5),
                'open_price': Decimal('10'),
                'high_price': Decimal('12'),
                'low_price': Decimal('9'),
                'close_price': Decimal('11'),
                'volume': Decimal('100'),
            }
            for i in range(10)
        ]

    def test_load_twice_same_count(self):
        from pipeline.loaders.fl001 import load
        start = T0
        end = T0 + timedelta(minutes=50)

        load('IDEM_OHLCV', start, end, self.canonical)
        count_after_first = OHLCVCandle.objects.filter(coin_id='IDEM_OHLCV').count()
        self.assertEqual(count_after_first, 10)

        load('IDEM_OHLCV', start, end, self.canonical)
        count_after_second = OHLCVCandle.objects.filter(coin_id='IDEM_OHLCV').count()
        self.assertEqual(count_after_second, 10, "Delete-write should not create duplicates")


class UniverseLoaderIdempotencyTest(TestCase):
    """Universe loader uses upsert — re-running must not create duplicates."""

    def test_upsert_twice_same_count(self):
        from pipeline.loaders.u001_universe import load_graduated_tokens

        records = [{
            'mint_address': 'IDEM_COIN',
            'anchor_event': T0,
            'name': 'Test Token',
            'symbol': 'TEST',
            'decimals': 6,
            'logo_url': None,
        }]

        load_graduated_tokens(records)
        count_after_first = MigratedCoin.objects.filter(mint_address='IDEM_COIN').count()
        self.assertEqual(count_after_first, 1)

        load_graduated_tokens(records)
        count_after_second = MigratedCoin.objects.filter(mint_address='IDEM_COIN').count()
        self.assertEqual(count_after_second, 1, "Upsert should not create duplicates")


class PoolMappingIdempotencyTest(TestCase):
    """Pool mapping loader uses upsert — re-running must not create duplicates."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='IDEM_POOL', anchor_event=T0,
        )

    def test_upsert_twice_same_count(self):
        from pipeline.loaders.u001_pool_mapping import load_pool_mappings

        records = [{
            'coin_id': 'IDEM_POOL',
            'pool_address': 'POOL123',
            'dex': 'pumpswap',
            'source': 'dexscreener',
            'created_at': T0,
        }]

        load_pool_mappings(records)
        count_after_first = PoolMapping.objects.filter(coin_id='IDEM_POOL').count()
        self.assertEqual(count_after_first, 1)

        load_pool_mappings(records)
        count_after_second = PoolMapping.objects.filter(coin_id='IDEM_POOL').count()
        self.assertEqual(count_after_second, 1, "Upsert should not create duplicates")
