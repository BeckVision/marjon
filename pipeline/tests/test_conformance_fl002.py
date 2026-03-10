"""Tests for FL-002 conformance function (Moralis -> HolderSnapshot)."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from pipeline.conformance.fl002_moralis import conform

FIXTURE_PATH = (
    Path(__file__).parent / 'fixtures' / 'moralis_holders_sample.json'
)


class FL002ConformanceTest(TestCase):
    def setUp(self):
        with open(FIXTURE_PATH) as f:
            self.raw = json.load(f)
        self.mint = "TEST_MINT_CONFORM_FL002"
        self.result = conform(self.raw, self.mint)

    def test_record_count(self):
        self.assertEqual(len(self.result), 5)

    def test_keys(self):
        expected_keys = {
            'timestamp', 'coin_id',
            'total_holders', 'net_holder_change',
            'holder_percent_change',
            'acquired_via_swap', 'acquired_via_transfer',
            'acquired_via_airdrop',
            'holders_in_whales', 'holders_in_sharks',
            'holders_in_dolphins', 'holders_in_fish',
            'holders_in_octopus', 'holders_in_crabs',
            'holders_in_shrimps',
            'holders_out_whales', 'holders_out_sharks',
            'holders_out_dolphins', 'holders_out_fish',
            'holders_out_octopus', 'holders_out_crabs',
            'holders_out_shrimps',
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
            datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(r['total_holders'], 1523)
        self.assertEqual(r['net_holder_change'], 15)
        self.assertEqual(
            r['holder_percent_change'], Decimal('0.99')
        )
        self.assertEqual(r['acquired_via_swap'], 12)
        self.assertEqual(r['acquired_via_transfer'], 3)
        self.assertEqual(r['acquired_via_airdrop'], 0)

    def test_nested_holders_in(self):
        r = self.result[0]
        self.assertEqual(r['holders_in_whales'], 0)
        self.assertEqual(r['holders_in_sharks'], 1)
        self.assertEqual(r['holders_in_dolphins'], 2)
        self.assertEqual(r['holders_in_fish'], 5)
        self.assertEqual(r['holders_in_octopus'], 3)
        self.assertEqual(r['holders_in_crabs'], 4)
        self.assertEqual(r['holders_in_shrimps'], 8)

    def test_nested_holders_out(self):
        r = self.result[0]
        self.assertEqual(r['holders_out_whales'], 0)
        self.assertEqual(r['holders_out_sharks'], 0)
        self.assertEqual(r['holders_out_dolphins'], 1)
        self.assertEqual(r['holders_out_fish'], 2)
        self.assertEqual(r['holders_out_octopus'], 1)
        self.assertEqual(r['holders_out_crabs'], 2)
        self.assertEqual(r['holders_out_shrimps'], 2)

    def test_holder_percent_change_is_decimal(self):
        for record in self.result:
            if record['holder_percent_change'] is not None:
                self.assertIsInstance(
                    record['holder_percent_change'], Decimal
                )

    def test_coin_id(self):
        for record in self.result:
            self.assertEqual(record['coin_id'], self.mint)

    def test_dead_coin_interval(self):
        """Third record has zero changes — dead coin interval."""
        r = self.result[2]
        self.assertEqual(r['net_holder_change'], 0)
        self.assertEqual(
            r['holder_percent_change'], Decimal('0.0')
        )
        self.assertEqual(r['holders_in_whales'], 0)
        self.assertEqual(r['holders_out_shrimps'], 0)
