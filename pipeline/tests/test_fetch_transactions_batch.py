from datetime import datetime, timedelta, timezone

from django.test import TestCase

from pipeline.management.commands.fetch_transactions_batch import _get_active_coins
from warehouse.models import (
    MigratedCoin,
    PipelineCompleteness,
    PoolMapping,
    U001PipelineStatus,
)


class FetchTransactionsBatchSelectionTest(TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)

        self.error_recent = MigratedCoin.objects.create(
            mint_address='ERR_RECENT',
            anchor_event=now - timedelta(hours=12),
        )
        self.partial_recent = MigratedCoin.objects.create(
            mint_address='PARTIAL_RECENT',
            anchor_event=now - timedelta(hours=10),
        )
        self.pending_recent = MigratedCoin.objects.create(
            mint_address='PENDING_RECENT',
            anchor_event=now - timedelta(hours=8),
        )
        self.complete_recent = MigratedCoin.objects.create(
            mint_address='COMPLETE_RECENT',
            anchor_event=now - timedelta(hours=6),
        )
        self.error_old = MigratedCoin.objects.create(
            mint_address='ERR_OLD',
            anchor_event=now - timedelta(days=10),
        )

        for coin in (
            self.error_recent,
            self.partial_recent,
            self.pending_recent,
            self.complete_recent,
            self.error_old,
        ):
            PoolMapping.objects.create(
                coin=coin,
                pool_address=f'POOL_{coin.mint_address}',
                dex='pumpswap',
                source='test',
            )

        U001PipelineStatus.objects.create(
            coin=self.error_recent,
            layer_id='RD-001',
            status=PipelineCompleteness.ERROR,
        )
        U001PipelineStatus.objects.create(
            coin=self.partial_recent,
            layer_id='RD-001',
            status=PipelineCompleteness.PARTIAL,
        )
        U001PipelineStatus.objects.create(
            coin=self.complete_recent,
            layer_id='RD-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        U001PipelineStatus.objects.create(
            coin=self.error_old,
            layer_id='RD-001',
            status=PipelineCompleteness.ERROR,
        )

    def test_incomplete_filter_returns_recent_incomplete_coins(self):
        coins = _get_active_coins(source='auto', status_filter='incomplete')
        self.assertCountEqual(
            [coin.mint_address for coin in coins],
            ['ERR_RECENT', 'PARTIAL_RECENT', 'PENDING_RECENT'],
        )

    def test_error_filter_returns_only_recent_error_coins_for_shyft_window(self):
        coins = _get_active_coins(source='auto', status_filter='error')
        self.assertEqual([coin.mint_address for coin in coins], ['ERR_RECENT'])

    def test_partial_filter_returns_only_recent_partial_coins(self):
        coins = _get_active_coins(source='auto', status_filter='partial')
        self.assertEqual([coin.mint_address for coin in coins], ['PARTIAL_RECENT'])

    def test_error_filter_can_select_old_coin_for_helius_mode(self):
        coins = _get_active_coins(source='helius', status_filter='error')
        self.assertEqual([coin.mint_address for coin in coins], ['ERR_OLD'])
