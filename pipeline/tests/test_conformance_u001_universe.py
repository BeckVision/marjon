"""Tests for U-001 universe conformance (Moralis graduated -> MigratedCoin)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from django.test import TestCase

from pipeline.conformance.u001_universe_moralis import conform_moralis_graduated

FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'u001' / 'moralis_graduated_sample.json'


class U001ConformanceTest(TestCase):
    def setUp(self):
        with open(FIXTURE_PATH) as f:
            data = json.load(f)
        self.raw_tokens = data['result']
        self.result = conform_moralis_graduated(self.raw_tokens)

    def test_record_count(self):
        self.assertEqual(len(self.result), 5)

    def test_keys(self):
        expected_keys = {
            'mint_address', 'anchor_event', 'name', 'symbol',
            'decimals', 'logo_url',
        }
        for record in self.result:
            self.assertEqual(set(record.keys()), expected_keys)

    def test_mint_address_is_string(self):
        for record in self.result:
            self.assertIsInstance(record['mint_address'], str)

    def test_anchor_event_is_utc_aware(self):
        for record in self.result:
            self.assertIsNotNone(record['anchor_event'].tzinfo)

    def test_decimals_is_int(self):
        for record in self.result:
            if record['decimals'] is not None:
                self.assertIsInstance(record['decimals'], int)

    def test_logo_url_is_string_or_none(self):
        for record in self.result:
            self.assertTrue(
                record['logo_url'] is None
                or isinstance(record['logo_url'], str)
            )

    def test_first_token_values(self):
        r = self.result[0]
        self.assertEqual(
            r['mint_address'],
            '96dCyTmXNmd9uSTQF9E93PXBxRUMk9jRXmzr5F26pump',
        )
        self.assertEqual(r['name'], 'Canabiii')
        self.assertEqual(r['symbol'], 'Canabiii')
        self.assertEqual(r['decimals'], 6)
        self.assertEqual(
            r['anchor_event'],
            datetime(2026, 3, 10, 17, 22, 7, tzinfo=timezone.utc),
        )

    def test_null_logo_handled(self):
        """Conformance handles logo=null without crashing."""
        raw = [{
            'tokenAddress': 'TESTnulllogo123',
            'graduatedAt': '2026-03-10T12:00:00.000Z',
            'name': 'Test',
            'symbol': 'TST',
            'decimals': '6',
            'logo': None,
        }]
        result = conform_moralis_graduated(raw)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]['logo_url'])

    def test_decimals_zero(self):
        """Clawcoin has decimals="0" — must parse to int 0, not None."""
        # Clawcoin is the third token in the fixture
        r = self.result[2]
        self.assertEqual(r['name'], 'Clawcoin')
        self.assertEqual(r['decimals'], 0)
        self.assertIsInstance(r['decimals'], int)

    def test_missing_required_field_crashes(self):
        """PDP6: missing tokenAddress must crash, not silently skip."""
        raw = [{
            'graduatedAt': '2026-03-10T12:00:00.000Z',
            'name': 'Missing',
            'symbol': 'MISS',
            'decimals': '6',
            'logo': None,
        }]
        with self.assertRaises(KeyError):
            conform_moralis_graduated(raw)

    def test_missing_graduated_at_crashes(self):
        """PDP6: missing graduatedAt must crash."""
        raw = [{
            'tokenAddress': 'TESTmissing123',
            'name': 'Missing',
            'symbol': 'MISS',
            'decimals': '6',
            'logo': None,
        }]
        with self.assertRaises(KeyError):
            conform_moralis_graduated(raw)

    def test_price_fields_excluded(self):
        """Conformance output must NOT contain live price fields."""
        excluded = {'priceUsd', 'priceNative', 'liquidity', 'fullyDilutedValuation'}
        for record in self.result:
            for field in excluded:
                self.assertNotIn(field, record)
