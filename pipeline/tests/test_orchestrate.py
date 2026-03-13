"""Tests for the pipeline orchestrator."""

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone as dj_timezone

from pipeline.orchestration.utils import (
    get_coins_to_process,
    load_universe_config,
    resolve_step_order,
    should_skip,
)
from warehouse.models import (
    MigratedCoin, OHLCVCandle, PipelineBatchRun, PipelineCompleteness,
    PoolMapping, RunStatus, U001PipelineStatus,
)

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


# --- Utils tests -----------------------------------------------------------

class LoadConfigTest(TestCase):
    def test_load_u001_config(self):
        config = load_universe_config('u001')
        self.assertEqual(config['id'], 'U-001')
        self.assertIn('discovery', config)
        self.assertIn('steps', config)
        self.assertIsInstance(config['steps'], list)

    def test_load_nonexistent_config(self):
        with self.assertRaises(ValueError):
            load_universe_config('nonexistent')


class ResolveStepOrderTest(TestCase):
    def test_steps_ordered_by_dependency(self):
        config = load_universe_config('u001')
        steps = resolve_step_order(config)
        names = [s['name'] for s in steps]
        # pool_mapping must come before ohlcv
        self.assertLess(names.index('pool_mapping'), names.index('ohlcv'))

    def test_filter_to_requested_steps(self):
        config = load_universe_config('u001')
        steps = resolve_step_order(config, requested_steps={'ohlcv'})
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]['name'], 'ohlcv')


class ShouldSkipTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='SKIP_TEST_COIN', anchor_event=T0,
        )

    def test_skip_pool_mapping_exists(self):
        PoolMapping.objects.create(
            coin_id='SKIP_TEST_COIN',
            pool_address='POOL_123',
            dex='pumpswap',
            source='dexpaprika',
        )
        step = {'name': 'pool_mapping', 'skip_if': 'pool_mapping_exists'}
        self.assertTrue(should_skip(self.coin, step))

    def test_skip_window_complete(self):
        U001PipelineStatus.objects.create(
            coin_id='SKIP_TEST_COIN',
            layer_id='FL-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        step = {'name': 'ohlcv', 'layer_id': 'FL-001', 'skip_if': 'window_complete'}
        self.assertTrue(should_skip(self.coin, step))

    def test_no_skip_when_not_started(self):
        step = {'name': 'ohlcv', 'layer_id': 'FL-001', 'skip_if': 'window_complete'}
        self.assertFalse(should_skip(self.coin, step))

    def test_no_skip_when_partial(self):
        U001PipelineStatus.objects.create(
            coin_id='SKIP_TEST_COIN',
            layer_id='FL-001',
            status=PipelineCompleteness.PARTIAL,
        )
        step = {'name': 'ohlcv', 'layer_id': 'FL-001', 'skip_if': 'window_complete'}
        self.assertFalse(should_skip(self.coin, step))

    def test_no_skip_when_error(self):
        U001PipelineStatus.objects.create(
            coin_id='SKIP_TEST_COIN',
            layer_id='FL-001',
            status=PipelineCompleteness.ERROR,
        )
        step = {'name': 'ohlcv', 'layer_id': 'FL-001', 'skip_if': 'window_complete'}
        self.assertFalse(should_skip(self.coin, step))


class ShouldSkipWindowCompleteOrImmatureTest(TestCase):
    """Tests for the window_complete_or_immature skip condition."""

    STEP = {'name': 'ohlcv', 'layer_id': 'FL-001', 'skip_if': 'window_complete_or_immature'}

    def test_skip_immature_coin(self):
        """Coin graduated 1 hour ago — window still open, skip."""
        coin = MigratedCoin.objects.create(
            mint_address='IMMATURE_COIN',
            anchor_event=dj_timezone.now() - timedelta(hours=1),
        )
        self.assertTrue(should_skip(coin, self.STEP))

    def test_no_skip_mature_coin(self):
        """Coin graduated 6 days ago — window closed, process it."""
        coin = MigratedCoin.objects.create(
            mint_address='MATURE_COIN',
            anchor_event=dj_timezone.now() - timedelta(days=6),
        )
        self.assertFalse(should_skip(coin, self.STEP))

    def test_skip_mature_coin_already_complete(self):
        """Mature coin with window_complete status — already done, skip."""
        coin = MigratedCoin.objects.create(
            mint_address='DONE_COIN',
            anchor_event=dj_timezone.now() - timedelta(days=6),
        )
        U001PipelineStatus.objects.create(
            coin_id='DONE_COIN',
            layer_id='FL-001',
            status=PipelineCompleteness.WINDOW_COMPLETE,
        )
        self.assertTrue(should_skip(coin, self.STEP))


class GetCoinsTest(TestCase):
    def setUp(self):
        self.old = MigratedCoin.objects.create(
            mint_address='OLD_COIN',
            anchor_event=T0 - timedelta(days=30),
        )
        self.recent = MigratedCoin.objects.create(
            mint_address='RECENT_COIN',
            anchor_event=T0,
        )

    def test_days_filter(self):
        with patch('pipeline.orchestration.utils.timezone') as mock_tz:
            mock_tz.now.return_value = T0 + timedelta(days=1)
            coins = get_coins_to_process({}, days=7)
        mints = [c.mint_address for c in coins]
        self.assertIn('RECENT_COIN', mints)
        self.assertNotIn('OLD_COIN', mints)

    def test_coins_limit(self):
        for i in range(10):
            MigratedCoin.objects.create(
                mint_address=f'LIMIT_COIN_{i}',
                anchor_event=T0,
            )
        coins = get_coins_to_process({}, max_coins=5)
        self.assertEqual(len(coins), 5)


# --- Orchestrator command tests ---------------------------------------------

class DryRunTest(TestCase):
    def setUp(self):
        MigratedCoin.objects.create(
            mint_address='DRY_COIN_1', anchor_event=T0,
        )
        MigratedCoin.objects.create(
            mint_address='DRY_COIN_2', anchor_event=T0,
        )

    def test_dry_run_creates_no_records(self):
        out = io.StringIO()
        call_command(
            'orchestrate', universe='u001', dry_run=True,
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn('DRY RUN', output)
        self.assertEqual(PipelineBatchRun.objects.count(), 0)
        self.assertEqual(U001PipelineStatus.objects.count(), 0)


# --- Handler tests ----------------------------------------------------------

class RunOHLCVHandlerTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='HANDLER_OHLCV', anchor_event=T0,
        )
        PoolMapping.objects.create(
            coin_id='HANDLER_OHLCV',
            pool_address='POOL_HANDLER',
            dex='pumpswap',
            source='dexpaprika',
            created_at=T0,
        )

    @patch('pipeline.management.commands.fetch_ohlcv.fetch_ohlcv')
    def test_run_ohlcv_succeeds(self, mock_fetch):
        mock_fetch.return_value = (
            [
                [1772359200, 10.0, 12.0, 9.0, 11.0, 100.0],
                [1772359500, 11.0, 13.0, 10.0, 12.0, 200.0],
            ],
            {'api_calls': 1},
        )

        from pipeline.orchestration.handlers import run_ohlcv
        result = run_ohlcv(self.coin, {})

        self.assertEqual(result['records_loaded'], 2)
        self.assertIn(result['status'], [
            PipelineCompleteness.PARTIAL,
            PipelineCompleteness.WINDOW_COMPLETE,
            'complete',
        ])
        self.assertIsNone(result['error_message'])

    @patch('pipeline.management.commands.fetch_ohlcv.fetch_ohlcv')
    def test_run_ohlcv_fails(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("Connection timeout")

        from pipeline.orchestration.handlers import run_ohlcv
        with self.assertRaises(RuntimeError):
            run_ohlcv(self.coin, {})


class RunPoolMappingHandlerTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='HANDLER_POOL', anchor_event=T0,
        )

    @patch('pipeline.management.commands.populate_pool_mapping.dex_fetch')
    @patch('pipeline.management.commands.populate_pool_mapping.gt_fetch')
    def test_run_pool_mapping_all_already_mapped(self, mock_gt, mock_dex):
        """When all coins already have mappings, nothing is fetched."""
        PoolMapping.objects.create(
            coin_id='HANDLER_POOL',
            pool_address='EXISTING_POOL',
            dex='pumpswap',
            source='dexpaprika',
        )

        # Dexscreener returns no pumpswap pairs for this mint
        mock_dex.return_value = ([], {'api_calls': 1})
        mock_gt.return_value = ({'data': [], 'included': []}, {'api_calls': 1})

        from pipeline.orchestration.handlers import run_pool_mapping
        result = run_pool_mapping([self.coin], {})
        self.assertIn('dexscreener_mapped', result)
        self.assertIn('geckoterminal_mapped', result)
        self.assertIn('unmapped', result)
