"""Tests for pool mapping loader (upsert into PoolMapping)."""

from datetime import datetime, timezone

from django.test import TestCase

from pipeline.loaders.u001_pool_mapping import load_pool_mappings
from warehouse.models import MigratedCoin, PoolMapping

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)


class PoolMappingLoaderTest(TestCase):
    def setUp(self):
        MigratedCoin.objects.create(
            mint_address='LOADER_COIN_1', anchor_event=T0,
        )
        MigratedCoin.objects.create(
            mint_address='LOADER_COIN_2', anchor_event=T0,
        )
        MigratedCoin.objects.create(
            mint_address='LOADER_COIN_3', anchor_event=T0,
        )

    def test_create_new_mapping(self):
        mappings = [{
            'coin_id': 'LOADER_COIN_1',
            'pool_address': 'POOL_NEW_1',
            'dex': 'pumpswap',
            'source': 'dexscreener',
            'created_at': T0,
        }]
        created, updated = load_pool_mappings(mappings)
        self.assertEqual(created, 1)
        self.assertEqual(updated, 0)

        row = PoolMapping.objects.get(
            coin_id='LOADER_COIN_1', pool_address='POOL_NEW_1',
        )
        self.assertEqual(row.dex, 'pumpswap')
        self.assertEqual(row.source, 'dexscreener')
        self.assertEqual(row.created_at, T0)

    def test_upsert_updates_existing(self):
        PoolMapping.objects.create(
            coin_id='LOADER_COIN_1',
            pool_address='POOL_EXISTING',
            dex='pumpswap',
            source='dexscreener',
            created_at=T0,
        )

        mappings = [{
            'coin_id': 'LOADER_COIN_1',
            'pool_address': 'POOL_EXISTING',
            'dex': 'pumpswap',
            'source': 'geckoterminal',
            'created_at': T1,
        }]
        created, updated = load_pool_mappings(mappings)
        self.assertEqual(created, 0)
        self.assertEqual(updated, 1)

        row = PoolMapping.objects.get(
            coin_id='LOADER_COIN_1', pool_address='POOL_EXISTING',
        )
        self.assertEqual(row.source, 'geckoterminal')

    def test_source_field_set_correctly(self):
        mappings = [{
            'coin_id': 'LOADER_COIN_2',
            'pool_address': 'POOL_SRC_TEST',
            'dex': 'pumpswap',
            'source': 'dexscreener',
            'created_at': T0,
        }]
        load_pool_mappings(mappings)
        row = PoolMapping.objects.get(
            coin_id='LOADER_COIN_2', pool_address='POOL_SRC_TEST',
        )
        self.assertEqual(row.source, 'dexscreener')

    def test_batch_return_counts(self):
        PoolMapping.objects.create(
            coin_id='LOADER_COIN_3',
            pool_address='POOL_BATCH_EXISTING',
            dex='pumpswap',
            source='dexscreener',
            created_at=T0,
        )

        mappings = [
            {
                'coin_id': 'LOADER_COIN_1',
                'pool_address': 'POOL_BATCH_NEW_1',
                'dex': 'pumpswap',
                'source': 'dexscreener',
                'created_at': T0,
            },
            {
                'coin_id': 'LOADER_COIN_2',
                'pool_address': 'POOL_BATCH_NEW_2',
                'dex': 'pumpswap',
                'source': 'geckoterminal',
                'created_at': T0,
            },
            {
                'coin_id': 'LOADER_COIN_3',
                'pool_address': 'POOL_BATCH_EXISTING',
                'dex': 'pumpswap',
                'source': 'geckoterminal',
                'created_at': T1,
            },
        ]
        created, updated = load_pool_mappings(mappings)
        self.assertEqual(created, 2)
        self.assertEqual(updated, 1)
