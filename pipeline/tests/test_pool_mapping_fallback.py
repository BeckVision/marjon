"""Integration tests for the pool mapping fallback chain."""

from datetime import datetime, timezone
from unittest.mock import patch

from django.test import TestCase

from pipeline.management.commands.populate_pool_mapping import run_fallback_chain
from warehouse.models import MigratedCoin, PoolMapping

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

MINT_1 = 'FALLBACK_MINT_1'
MINT_2 = 'FALLBACK_MINT_2'
MINT_3 = 'FALLBACK_MINT_3'
MINT_4 = 'FALLBACK_MINT_4'
MINT_5 = 'FALLBACK_MINT_5'


def _dex_pairs_for(mints):
    """Build Dexscreener-shaped pair dicts for given mint addresses."""
    return [
        {
            'dexId': 'pumpswap',
            'baseToken': {'address': m},
            'pairAddress': f'DEX_POOL_{m}',
            'pairCreatedAt': 1772790925000,
        }
        for m in mints
    ]


def _gt_response_for(mints):
    """Build GeckoTerminal-shaped response for given mint addresses."""
    data = []
    included = []
    for m in mints:
        pool_id = f'solana_GT_POOL_{m}'
        data.append({
            'id': f'solana_{m}',
            'type': 'token',
            'attributes': {'address': m},
            'relationships': {
                'top_pools': {'data': [{'id': pool_id, 'type': 'pool'}]},
            },
        })
        included.append({
            'id': pool_id,
            'type': 'pool',
            'attributes': {
                'address': f'GT_POOL_{m}',
                'pool_created_at': '2026-03-01T00:00:00Z',
            },
            'relationships': {
                'base_token': {'data': {'id': f'solana_{m}', 'type': 'token'}},
                'dex': {'data': {'id': 'pumpswap', 'type': 'dex'}},
            },
        })
    return {'data': data, 'included': included}


class FallbackChainTest(TestCase):
    def setUp(self):
        for mint in [MINT_1, MINT_2, MINT_3, MINT_4, MINT_5]:
            MigratedCoin.objects.create(
                mint_address=mint, anchor_event=T0,
            )

    @patch('pipeline.management.commands.populate_pool_mapping.gt_fetch')
    @patch('pipeline.management.commands.populate_pool_mapping.dex_fetch')
    def test_stage1_maps_some_tokens(self, mock_dex, mock_gt):
        # Dexscreener maps 3 of 5
        mock_dex.return_value = (
            _dex_pairs_for([MINT_1, MINT_2, MINT_3]),
            {'api_calls': 1},
        )
        # GeckoTerminal maps 1 of the remaining 2
        mock_gt.return_value = (
            _gt_response_for([MINT_4]),
            {'api_calls': 1},
        )

        result = run_fallback_chain([MINT_1, MINT_2, MINT_3, MINT_4, MINT_5])
        self.assertEqual(result['dexscreener_mapped'], 3)
        self.assertEqual(result['geckoterminal_mapped'], 1)
        self.assertEqual(result['unmapped'], 1)

    @patch('pipeline.management.commands.populate_pool_mapping.gt_fetch')
    @patch('pipeline.management.commands.populate_pool_mapping.dex_fetch')
    def test_stage2_only_receives_misses(self, mock_dex, mock_gt):
        # Dexscreener maps MINT_1, MINT_2, MINT_3
        mock_dex.return_value = (
            _dex_pairs_for([MINT_1, MINT_2, MINT_3]),
            {'api_calls': 1},
        )
        # GeckoTerminal should only be called with MINT_4, MINT_5
        mock_gt.return_value = (
            _gt_response_for([MINT_4]),
            {'api_calls': 1},
        )

        run_fallback_chain([MINT_1, MINT_2, MINT_3, MINT_4, MINT_5])

        mock_gt.assert_called_once()
        gt_call_args = mock_gt.call_args[0][0]
        self.assertEqual(sorted(gt_call_args), sorted([MINT_4, MINT_5]))

    @patch('pipeline.management.commands.populate_pool_mapping.gt_fetch')
    @patch('pipeline.management.commands.populate_pool_mapping.dex_fetch')
    def test_all_mapped_by_stage1_skips_stage2(self, mock_dex, mock_gt):
        mock_dex.return_value = (
            _dex_pairs_for([MINT_1, MINT_2, MINT_3, MINT_4, MINT_5]),
            {'api_calls': 1},
        )

        result = run_fallback_chain([MINT_1, MINT_2, MINT_3, MINT_4, MINT_5])
        self.assertEqual(result['dexscreener_mapped'], 5)
        self.assertEqual(result['unmapped'], 0)
        mock_gt.assert_not_called()

    @patch('pipeline.management.commands.populate_pool_mapping.gt_fetch')
    @patch('pipeline.management.commands.populate_pool_mapping.dex_fetch')
    def test_no_unmapped_tokens_returns_early(self, mock_dex, mock_gt):
        # Pre-create mappings for all 5
        for mint in [MINT_1, MINT_2, MINT_3, MINT_4, MINT_5]:
            PoolMapping.objects.create(
                coin_id=mint,
                pool_address=f'EXISTING_POOL_{mint}',
                dex='pumpswap',
                source='dexscreener',
            )

        # Call with no args — should auto-detect all mapped
        result = run_fallback_chain()
        self.assertEqual(result['total_processed'], 0)
        mock_dex.assert_not_called()
        mock_gt.assert_not_called()

    @patch('pipeline.management.commands.populate_pool_mapping.gt_fetch')
    @patch('pipeline.management.commands.populate_pool_mapping.dex_fetch')
    def test_db_rows_created(self, mock_dex, mock_gt):
        mock_dex.return_value = (
            _dex_pairs_for([MINT_1, MINT_2, MINT_3]),
            {'api_calls': 1},
        )
        mock_gt.return_value = (
            _gt_response_for([MINT_4]),
            {'api_calls': 1},
        )

        run_fallback_chain([MINT_1, MINT_2, MINT_3, MINT_4, MINT_5])

        self.assertEqual(PoolMapping.objects.count(), 4)

        # Verify source is correct per row
        for mint in [MINT_1, MINT_2, MINT_3]:
            row = PoolMapping.objects.get(coin_id=mint)
            self.assertEqual(row.source, 'dexscreener')

        gt_row = PoolMapping.objects.get(coin_id=MINT_4)
        self.assertEqual(gt_row.source, 'geckoterminal')
