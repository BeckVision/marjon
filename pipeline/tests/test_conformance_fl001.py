"""Tests for FL-001 conformance function (DexPaprika -> OHLCVCandle)."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from pipeline.conformance.fl001_dexpaprika import conform

FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'dexpaprika_ohlcv_sample.json'


class FL001ConformanceTest(TestCase):
    def setUp(self):
        with open(FIXTURE_PATH) as f:
            self.raw = json.load(f)
        self.mint = "TEST_MINT_CONFORM"
        self.result = conform(self.raw, self.mint)

    def test_record_count(self):
        self.assertEqual(len(self.result), 5)

    def test_keys(self):
        expected_keys = {
            'timestamp', 'open_price', 'high_price', 'low_price',
            'close_price', 'volume', 'coin_id',
        }
        for record in self.result:
            self.assertEqual(set(record.keys()), expected_keys)

    def test_timestamp_is_utc_aware(self):
        for record in self.result:
            self.assertIsNotNone(record['timestamp'].tzinfo)

    def test_first_record_values(self):
        r = self.result[0]
        self.assertEqual(
            r['timestamp'],
            datetime(2026, 3, 7, 16, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(r['open_price'], Decimal('83.5'))
        self.assertEqual(r['high_price'], Decimal('84.2'))
        self.assertEqual(r['low_price'], Decimal('82.1'))
        self.assertEqual(r['close_price'], Decimal('83.9'))
        self.assertEqual(r['volume'], Decimal('125000'))

    def test_prices_are_decimal(self):
        for record in self.result:
            self.assertIsInstance(record['open_price'], Decimal)
            self.assertIsInstance(record['high_price'], Decimal)
            self.assertIsInstance(record['low_price'], Decimal)
            self.assertIsInstance(record['close_price'], Decimal)
            self.assertIsInstance(record['volume'], Decimal)

    def test_coin_id(self):
        for record in self.result:
            self.assertEqual(record['coin_id'], self.mint)

    def test_no_ingested_at_in_output(self):
        """ingested_at is handled by auto_now_add, not by conformance."""
        for record in self.result:
            self.assertNotIn('ingested_at', record)
