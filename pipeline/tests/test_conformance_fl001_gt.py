"""Tests for FL-001 conformance function (GeckoTerminal -> OHLCVCandle)."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from pipeline.conformance.fl001_geckoterminal import conform

FIXTURE_PATH = (
    Path(__file__).parent / 'fixtures' / 'u001' / 'geckoterminal_ohlcv_sample.json'
)


class FL001GeckoTerminalConformanceTest(TestCase):
    def setUp(self):
        with open(FIXTURE_PATH) as f:
            self.raw = json.load(f)
        self.mint = "TEST_MINT_GT"
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
            self.assertEqual(record['timestamp'].tzinfo, timezone.utc)

    def test_unix_timestamp_conversion(self):
        r = self.result[0]
        # 1772901000 = 2026-03-07T16:30:00Z
        expected = datetime(2026, 3, 5, 10, 30, tzinfo=timezone.utc)
        self.assertEqual(
            r['timestamp'],
            datetime.fromtimestamp(1772901000, tz=timezone.utc),
        )

    def test_first_record_values(self):
        r = self.result[0]
        self.assertEqual(
            r['open_price'],
            Decimal('0.0006595577862302356'),
        )
        self.assertEqual(
            r['high_price'],
            Decimal('0.0006728943210451234'),
        )
        self.assertEqual(
            r['low_price'],
            Decimal('0.0005224562374509502'),
        )
        self.assertEqual(
            r['close_price'],
            Decimal('0.0005224562374509502'),
        )
        self.assertEqual(
            r['volume'],
            Decimal('7040.654454485315'),
        )

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

    def test_no_extra_fields(self):
        """No trades, raw timestamp, or other fields in output."""
        allowed = {
            'timestamp', 'open_price', 'high_price', 'low_price',
            'close_price', 'volume', 'coin_id',
        }
        for record in self.result:
            self.assertEqual(set(record.keys()), allowed)

    def test_short_candle_array_crashes(self):
        """PDP6: candle with <6 elements must crash, not silently skip."""
        bad_input = [[1772901000, 10.0, 12.0, 9.0, 11.0]]  # 5 elements
        with self.assertRaises(IndexError):
            conform(bad_input, "TEST_MINT")


class GeckoTerminalConnectorMetadataTest(TestCase):
    def test_returns_metadata_with_api_calls(self):
        from unittest.mock import patch

        fake_response = {
            'data': {
                'attributes': {
                    'ohlcv_list': [
                        [1772359200, 10.0, 12.0, 9.0, 11.0, 100.0],
                    ],
                },
            },
        }

        with patch(
            'pipeline.connectors.geckoterminal.request_with_retry'
        ) as mock_request:
            mock_request.return_value = fake_response

            from pipeline.connectors.geckoterminal import fetch_ohlcv
            start = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
            end = datetime(2026, 3, 1, 10, 10, tzinfo=timezone.utc)

            records, meta = fetch_ohlcv('POOL_GT_TEST', start, end)

            self.assertIsInstance(records, list)
            self.assertEqual(len(records), 1)
            self.assertIn('api_calls', meta)
            self.assertGreaterEqual(meta['api_calls'], 1)
