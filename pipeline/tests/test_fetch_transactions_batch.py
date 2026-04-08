from datetime import datetime, timedelta, timezone

from django.test import TestCase

from pipeline.management.commands.fetch_transactions_batch import (
    _get_active_coins,
    _order_status_only_queue,
    _order_work_queue,
)
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

    def test_partial_and_error_recovery_prefers_oldest_then_smallest(self):
        status_last_run = {
            'ERR_RECENT': datetime(2026, 3, 15, tzinfo=timezone.utc),
            'PARTIAL_RECENT': datetime(2026, 3, 14, tzinfo=timezone.utc),
            'PENDING_RECENT': datetime(2026, 3, 14, 1, tzinfo=timezone.utc),
        }
        mint_sig_counts = {
            'ERR_RECENT': 50,
            'PARTIAL_RECENT': 200,
            'PENDING_RECENT': 20,
        }

        self.assertEqual(
            _order_work_queue(mint_sig_counts, status_last_run, 'partial'),
            ['PARTIAL_RECENT', 'PENDING_RECENT', 'ERR_RECENT'],
        )
        self.assertEqual(
            _order_work_queue(mint_sig_counts, status_last_run, 'error'),
            ['PARTIAL_RECENT', 'PENDING_RECENT', 'ERR_RECENT'],
        )

    def test_incomplete_queue_prefers_busiest(self):
        mint_sig_counts = {
            'A': 10,
            'B': 300,
            'C': 50,
        }
        self.assertEqual(
            _order_work_queue(mint_sig_counts, {}, 'incomplete'),
            ['B', 'C', 'A'],
        )

    def test_status_only_queue_prefers_oldest_last_run(self):
        status_last_run = {
            'A': datetime(2026, 3, 15, tzinfo=timezone.utc),
            'B': datetime(2026, 3, 14, tzinfo=timezone.utc),
        }
        self.assertEqual(
            _order_status_only_queue(['A', 'B', 'C'], status_last_run),
            ['C', 'B', 'A'],
        )
