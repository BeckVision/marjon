"""Tests for U-001 universe loader (upsert into MigratedCoin)."""

from datetime import datetime, timezone

from django.test import TestCase

from pipeline.loaders.u001_universe import load_graduated_tokens
from warehouse.models import MigratedCoin

T1 = datetime(2026, 3, 10, 17, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 3, 10, 18, 0, 0, tzinfo=timezone.utc)


class U001LoaderTest(TestCase):
    def test_create_new_token(self):
        """Loader creates a new MigratedCoin with all fields."""
        tokens = [{
            'mint_address': 'NEW_TOKEN_001',
            'anchor_event': T1,
            'name': 'Test Token',
            'symbol': 'TST',
            'decimals': 6,
            'logo_url': 'https://example.com/logo.png',
        }]
        created, updated = load_graduated_tokens(tokens)
        self.assertEqual(created, 1)
        self.assertEqual(updated, 0)

        coin = MigratedCoin.objects.get(mint_address='NEW_TOKEN_001')
        self.assertEqual(coin.name, 'Test Token')
        self.assertEqual(coin.symbol, 'TST')
        self.assertEqual(coin.decimals, 6)
        self.assertEqual(coin.logo_url, 'https://example.com/logo.png')
        self.assertEqual(coin.anchor_event, T1)

    def test_upsert_updates_metadata(self):
        """Re-encountering a token updates name but preserves anchor_event."""
        MigratedCoin.objects.create(
            mint_address='EXISTING_001',
            anchor_event=T1,
            name='Old Name',
            symbol='OLD',
            decimals=6,
        )

        tokens = [{
            'mint_address': 'EXISTING_001',
            'anchor_event': T2,
            'name': 'New Name',
            'symbol': 'NEW',
            'decimals': 9,
            'logo_url': 'https://example.com/new.png',
        }]
        created, updated = load_graduated_tokens(tokens)
        self.assertEqual(created, 0)
        self.assertEqual(updated, 1)

        coin = MigratedCoin.objects.get(mint_address='EXISTING_001')
        self.assertEqual(coin.name, 'New Name')
        self.assertEqual(coin.symbol, 'NEW')
        self.assertEqual(coin.decimals, 9)

    def test_anchor_event_never_overwritten(self):
        """CRITICAL: anchor_event is set only on create, never updated."""
        MigratedCoin.objects.create(
            mint_address='ANCHOR_TEST',
            anchor_event=T1,
            name='Original',
        )

        tokens = [{
            'mint_address': 'ANCHOR_TEST',
            'anchor_event': T2,
            'name': 'Updated',
            'symbol': 'UPD',
            'decimals': 6,
            'logo_url': None,
        }]
        load_graduated_tokens(tokens)

        coin = MigratedCoin.objects.get(mint_address='ANCHOR_TEST')
        self.assertEqual(coin.anchor_event, T1)  # NOT T2

    def test_batch_return_counts(self):
        """Loader returns correct (created, updated) counts."""
        MigratedCoin.objects.create(
            mint_address='BATCH_EXISTING',
            anchor_event=T1,
        )

        tokens = [
            {
                'mint_address': 'BATCH_NEW_1',
                'anchor_event': T1,
                'name': 'New 1',
                'symbol': 'N1',
                'decimals': 6,
                'logo_url': None,
            },
            {
                'mint_address': 'BATCH_NEW_2',
                'anchor_event': T1,
                'name': 'New 2',
                'symbol': 'N2',
                'decimals': 6,
                'logo_url': None,
            },
            {
                'mint_address': 'BATCH_EXISTING',
                'anchor_event': T2,
                'name': 'Updated Existing',
                'symbol': 'UPD',
                'decimals': 9,
                'logo_url': None,
            },
        ]
        created, updated = load_graduated_tokens(tokens)
        self.assertEqual(created, 2)
        self.assertEqual(updated, 1)
