"""Tests for pool mapping conformance functions (Dexscreener + GeckoTerminal)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from django.test import TestCase

from pipeline.conformance.u001_pool_mapping_dexscreener import conform as dex_conform
from pipeline.conformance.u001_pool_mapping_geckoterminal import conform as gt_conform

DEX_FIXTURE_PATH = (
    Path(__file__).parent / 'fixtures' / 'u001' / 'dexscreener_token_pools_sample.json'
)
GT_FIXTURE_PATH = (
    Path(__file__).parent / 'fixtures' / 'u001' / 'geckoterminal_token_pools_sample.json'
)


class DexscreenerPoolMappingConformanceTest(TestCase):
    def setUp(self):
        with open(DEX_FIXTURE_PATH) as f:
            self.raw = json.load(f)
        self.result = dex_conform(self.raw)

    def test_record_count(self):
        self.assertEqual(len(self.result), 3)

    def test_output_keys(self):
        expected_keys = {'coin_id', 'pool_address', 'dex', 'source', 'created_at'}
        for record in self.result:
            self.assertEqual(set(record.keys()), expected_keys)

    def test_coin_id_is_base_token_address(self):
        self.assertEqual(
            self.result[0]['coin_id'],
            "12qKJmoJj9hKs12S8kPhRMrWhsyfqaiEsDh9Z38xpump",
        )

    def test_pool_address(self):
        self.assertEqual(
            self.result[0]['pool_address'],
            "DDfjJU1XXLM84G32y9u4oHjXm6EDRBGaUXucfvALgWyu",
        )

    def test_dex_is_pumpswap(self):
        for record in self.result:
            self.assertEqual(record['dex'], 'pumpswap')

    def test_source_is_dexscreener(self):
        for record in self.result:
            self.assertEqual(record['source'], 'dexscreener')

    def test_created_at_is_utc_datetime(self):
        for record in self.result:
            self.assertIsInstance(record['created_at'], datetime)
            self.assertIsNotNone(record['created_at'].tzinfo)
            self.assertEqual(record['created_at'].tzinfo, timezone.utc)

    def test_created_at_millis_conversion(self):
        expected = datetime.fromtimestamp(1772790925000 / 1000, tz=timezone.utc)
        self.assertEqual(self.result[0]['created_at'], expected)

    def test_non_pumpswap_pair_excluded(self):
        pair = {
            'dexId': 'meteora',
            'baseToken': {'address': 'SOME_TOKEN'},
            'pairAddress': 'SOME_POOL',
            'pairCreatedAt': 1772790925000,
        }
        result = dex_conform([pair])
        self.assertEqual(len(result), 0)

    def test_mixed_dex_ids_filtered(self):
        pairs = [
            {
                'dexId': 'pumpswap',
                'baseToken': {'address': 'TOKEN_A'},
                'pairAddress': 'POOL_A',
                'pairCreatedAt': 1772790925000,
            },
            {
                'dexId': 'meteora',
                'baseToken': {'address': 'TOKEN_B'},
                'pairAddress': 'POOL_B',
                'pairCreatedAt': 1772790925000,
            },
            {
                'dexId': 'pumpswap',
                'baseToken': {'address': 'TOKEN_C'},
                'pairAddress': 'POOL_C',
                'pairCreatedAt': 1772790925000,
            },
        ]
        result = dex_conform(pairs)
        self.assertEqual(len(result), 2)

    def test_missing_required_field_crashes(self):
        pair = {
            'dexId': 'pumpswap',
            'pairAddress': 'SOME_POOL',
            'pairCreatedAt': 1772790925000,
        }
        with self.assertRaises(KeyError):
            dex_conform([pair])

    def test_missing_pair_address_crashes(self):
        pair = {
            'dexId': 'pumpswap',
            'baseToken': {'address': 'SOME_TOKEN'},
            'pairCreatedAt': 1772790925000,
        }
        with self.assertRaises(KeyError):
            dex_conform([pair])


class GeckoTerminalPoolMappingConformanceTest(TestCase):
    def setUp(self):
        with open(GT_FIXTURE_PATH) as f:
            self.raw = json.load(f)
        self.result = gt_conform(self.raw)

    def test_record_count(self):
        self.assertEqual(len(self.result), 2)

    def test_output_keys(self):
        expected_keys = {'coin_id', 'pool_address', 'dex', 'source', 'created_at'}
        for record in self.result:
            self.assertEqual(set(record.keys()), expected_keys)

    def test_solana_prefix_stripped(self):
        # HBAD token
        hbad = [r for r in self.result if 'iBz3' in r['coin_id']][0]
        self.assertEqual(
            hbad['coin_id'],
            "12iBz3EMnPb53wUFzYyX7M6b4LpdjBGsDwCzq3Kfpump",
        )

    def test_pool_address(self):
        hbad = [r for r in self.result if 'iBz3' in r['coin_id']][0]
        self.assertEqual(
            hbad['pool_address'],
            "36TATJSRxW7bzhJP6zcmgDMSSYZUwniY8ya8rvhsyhLp",
        )

    def test_dex_is_pumpswap(self):
        for record in self.result:
            self.assertEqual(record['dex'], 'pumpswap')

    def test_source_is_geckoterminal(self):
        for record in self.result:
            self.assertEqual(record['source'], 'geckoterminal')

    def test_created_at_is_utc_datetime(self):
        for record in self.result:
            self.assertIsInstance(record['created_at'], datetime)
            self.assertIsNotNone(record['created_at'].tzinfo)
            self.assertEqual(record['created_at'].tzinfo, timezone.utc)

    def test_created_at_iso_parsing(self):
        hbad = [r for r in self.result if 'iBz3' in r['coin_id']][0]
        expected = datetime(2026, 3, 3, 2, 45, 20, tzinfo=timezone.utc)
        self.assertEqual(hbad['created_at'], expected)

    def test_token_with_no_pools_skipped(self):
        coin_ids = [r['coin_id'] for r in self.result]
        # USIS has empty top_pools.data — should not appear
        self.assertNotIn('125FGR22fJitLyfKSMZ3QmbivS4dBpSZb2jTZWNhpump', coin_ids)

    def test_empty_included_returns_empty(self):
        response = {
            'data': [
                {
                    'id': 'solana_TEST',
                    'type': 'token',
                    'attributes': {'address': 'TEST'},
                    'relationships': {
                        'top_pools': {'data': [{'id': 'solana_POOL', 'type': 'pool'}]},
                    },
                },
            ],
        }
        result = gt_conform(response)
        self.assertEqual(result, [])

    def test_non_pumpswap_pool_excluded(self):
        response = {
            'data': [
                {
                    'id': 'solana_TOKEN',
                    'type': 'token',
                    'attributes': {'address': 'TOKEN'},
                    'relationships': {
                        'top_pools': {'data': [{'id': 'solana_POOL', 'type': 'pool'}]},
                    },
                },
            ],
            'included': [
                {
                    'id': 'solana_POOL',
                    'type': 'pool',
                    'attributes': {
                        'address': 'POOL_ADDR',
                        'pool_created_at': '2026-03-01T00:00:00Z',
                    },
                    'relationships': {
                        'base_token': {'data': {'id': 'solana_TOKEN', 'type': 'token'}},
                        'dex': {'data': {'id': 'meteora', 'type': 'dex'}},
                    },
                },
            ],
        }
        result = gt_conform(response)
        self.assertEqual(len(result), 0)

    def test_missing_pool_in_included_crashes(self):
        response = {
            'data': [
                {
                    'id': 'solana_TOKEN',
                    'type': 'token',
                    'attributes': {'address': 'TOKEN'},
                    'relationships': {
                        'top_pools': {
                            'data': [{'id': 'solana_MISSING_POOL', 'type': 'pool'}],
                        },
                    },
                },
            ],
            'included': [
                {
                    'id': 'solana_OTHER_POOL',
                    'type': 'pool',
                    'attributes': {
                        'address': 'OTHER',
                        'pool_created_at': '2026-03-01T00:00:00Z',
                    },
                    'relationships': {
                        'base_token': {'data': {'id': 'solana_TOKEN', 'type': 'token'}},
                        'dex': {'data': {'id': 'pumpswap', 'type': 'dex'}},
                    },
                },
            ],
        }
        with self.assertRaises(KeyError):
            gt_conform(response)
