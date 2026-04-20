from datetime import datetime, timedelta, timezone

from django.test import TestCase

from pipeline.management.commands.fetch_transactions_batch import (
    _apply_bootstrap_sig_cap,
    _apply_min_sig_thresholds,
    _get_active_coins,
    _order_status_only_queue,
    _order_work_queue,
)
from warehouse.models import (
    MigratedCoin,
    PipelineCompleteness,
    PoolMapping,
    RawTransaction,
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
        self.partial_old = MigratedCoin.objects.create(
            mint_address='PARTIAL_OLD',
            anchor_event=now - timedelta(days=12),
        )
        PoolMapping.objects.create(
            coin=self.partial_old,
            pool_address=f'POOL_{self.partial_old.mint_address}',
            dex='pumpswap',
            source='test',
        )
        U001PipelineStatus.objects.create(
            coin=self.partial_old,
            layer_id='RD-001',
            status=PipelineCompleteness.PARTIAL,
        )

    def test_incomplete_filter_returns_recent_incomplete_coins(self):
        coins = _get_active_coins(source='auto', status_filter='incomplete')
        self.assertCountEqual(
            [coin.mint_address for coin in coins],
            ['ERR_RECENT', 'PARTIAL_RECENT', 'PENDING_RECENT'],
        )

    def test_incomplete_filter_includes_recent_error_rows(self):
        recent_error = MigratedCoin.objects.create(
            mint_address='RECENT_ERROR',
            anchor_event=datetime.now(timezone.utc) - timedelta(hours=4),
        )
        PoolMapping.objects.create(
            coin=recent_error,
            pool_address='POOL_RECENT_ERROR',
            dex='pumpswap',
            source='test',
        )
        U001PipelineStatus.objects.create(
            coin=recent_error,
            layer_id='RD-001',
            status=PipelineCompleteness.ERROR,
        )

        coins = _get_active_coins(source='auto', status_filter='incomplete')

        self.assertIn('RECENT_ERROR', [coin.mint_address for coin in coins])

    def test_error_filter_returns_only_recent_error_coins_for_shyft_window(self):
        coins = _get_active_coins(source='auto', status_filter='error')
        self.assertEqual([coin.mint_address for coin in coins], ['ERR_RECENT'])

    def test_partial_filter_returns_only_recent_partial_coins(self):
        coins = _get_active_coins(source='auto', status_filter='partial')
        self.assertEqual([coin.mint_address for coin in coins], ['PARTIAL_RECENT'])

    def test_error_filter_can_select_old_coin_for_helius_mode(self):
        coins = _get_active_coins(source='helius', status_filter='error')
        self.assertEqual([coin.mint_address for coin in coins], ['ERR_OLD'])

    def test_candidate_limit_prefers_newest_recent_coins(self):
        coins = _get_active_coins(
            source='auto',
            status_filter='incomplete',
            candidate_limit=2,
        )
        self.assertEqual(
            [coin.mint_address for coin in coins],
            ['PENDING_RECENT', 'PARTIAL_RECENT'],
        )

    def test_candidate_limit_prefers_recent_coins_with_existing_raw_history(self):
        watermarked_recent = MigratedCoin.objects.create(
            mint_address='WATERMARKED_RECENT',
            anchor_event=datetime.now(timezone.utc) - timedelta(hours=20),
        )
        PoolMapping.objects.create(
            coin=watermarked_recent,
            pool_address='POOL_WATERMARKED_RECENT',
            dex='pumpswap',
            source='test',
        )
        RawTransaction.objects.create(
            coin=watermarked_recent,
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=10),
            tx_signature='sig-watermarked-recent',
            trade_type='BUY',
            wallet_address='wallet-watermarked',
            token_amount=1,
            sol_amount=1,
            pool_address='POOL_WATERMARKED_RECENT',
            tx_fee='0.000005',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )

        coins = _get_active_coins(
            source='auto',
            status_filter='incomplete',
            candidate_limit=1,
        )

        self.assertEqual(
            [coin.mint_address for coin in coins],
            ['WATERMARKED_RECENT'],
        )

    def test_partial_filter_returns_old_partial_coin_for_helius_mode(self):
        coins = _get_active_coins(source='helius', status_filter='partial')
        self.assertEqual([coin.mint_address for coin in coins], ['PARTIAL_OLD'])

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

    def test_incomplete_queue_prefers_steady_state_then_safe_bootstrap(self):
        mint_sig_counts = {
            'A': 10,
            'B': 300,
            'C': 50,
            'D': 20,
        }
        mint_has_watermark = {
            'A': False,
            'B': True,
            'C': False,
            'D': True,
        }
        self.assertEqual(
            _order_work_queue(
                mint_sig_counts, {}, 'incomplete', mint_has_watermark,
            ),
            ['B', 'D', 'A', 'C'],
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

    def test_status_only_error_queue_prefers_existing_raw_history(self):
        status_last_run = {
            'A': datetime(2026, 3, 13, tzinfo=timezone.utc),
            'B': datetime(2026, 3, 12, tzinfo=timezone.utc),
            'C': datetime(2026, 3, 11, tzinfo=timezone.utc),
        }
        raw_counts = {
            'A': 0,
            'B': 245,
            'C': 10,
        }
        self.assertEqual(
            _order_status_only_queue(['A', 'B', 'C'], status_last_run, raw_counts),
            ['C', 'B', 'A'],
        )

    def test_status_only_partial_queue_prefers_smaller_existing_raw_history(self):
        status_last_run = {
            'A': datetime(2026, 3, 13, tzinfo=timezone.utc),
            'B': datetime(2026, 3, 12, tzinfo=timezone.utc),
            'C': datetime(2026, 3, 11, tzinfo=timezone.utc),
        }
        raw_counts = {
            'A': 1200,
            'B': 45,
            'C': 66,
        }
        self.assertEqual(
            _order_status_only_queue(['A', 'B', 'C'], status_last_run, raw_counts),
            ['B', 'C', 'A'],
        )

    def test_bootstrap_sig_cap_skips_risky_bootstrap_only(self):
        mint_sig_counts = {
            'SAFE_STEADY': 700,
            'SAFE_BOOTSTRAP': 300,
            'RISKY_BOOTSTRAP': 450,
        }
        mint_has_watermark = {
            'SAFE_STEADY': True,
            'SAFE_BOOTSTRAP': False,
            'RISKY_BOOTSTRAP': False,
        }

        kept, skipped = _apply_bootstrap_sig_cap(
            mint_sig_counts,
            mint_has_watermark,
            bootstrap_max_new_sigs=400,
        )

        self.assertEqual(
            kept,
            {
                'SAFE_STEADY': 700,
                'SAFE_BOOTSTRAP': 300,
            },
        )
        self.assertEqual(
            skipped,
            {'RISKY_BOOTSTRAP': 450},
        )

    def test_min_sig_thresholds_allow_small_steady_state_deltas(self):
        mint_sig_counts = {
            'BOOTSTRAP_TOO_SMALL': 2,
            'BOOTSTRAP_OK': 3,
            'STEADY_TINY': 1,
            'STEADY_ZERO': 0,
        }
        mint_has_watermark = {
            'BOOTSTRAP_TOO_SMALL': False,
            'BOOTSTRAP_OK': False,
            'STEADY_TINY': True,
            'STEADY_ZERO': True,
        }

        kept, skipped = _apply_min_sig_thresholds(
            mint_sig_counts,
            mint_has_watermark,
            min_sigs=3,
            min_steady_state_sigs=1,
        )

        self.assertEqual(
            kept,
            {
                'BOOTSTRAP_OK': 3,
                'STEADY_TINY': 1,
            },
        )
        self.assertEqual(
            skipped,
            {
                'BOOTSTRAP_TOO_SMALL': 2,
                'STEADY_ZERO': 0,
            },
        )
