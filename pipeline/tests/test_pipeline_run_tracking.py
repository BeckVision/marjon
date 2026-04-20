"""Tests for U001PipelineRun tracking in fetch_ohlcv and fetch_holders."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from warehouse.models import (
    MigratedCoin, OHLCVCandle, PipelineCompleteness, PoolMapping,
    RunMode, RunStatus, U001PipelineRun, U001PipelineStatus,
)

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


# --- Fixtures ----------------------------------------------------------------

# GeckoTerminal format: [unix_timestamp, open, high, low, close, volume]
FAKE_OHLCV_RAW = [
    [1772359200, 10.0, 12.0, 9.0, 11.0, 100.0],  # 2026-03-01T10:00:00Z
    [1772359500, 11.0, 13.0, 10.0, 12.0, 200.0],  # 2026-03-01T10:05:00Z
]

FAKE_HOLDER_RAW = [
    {
        'timestamp': '2026-03-01T10:00:00.000Z',
        'totalHolders': 100,
        'netHolderChange': 5,
        'holderPercentChange': 5.0,
        'newHoldersByAcquisition': {'swap': 3, 'transfer': 1, 'airdrop': 1},
        'holdersIn': {
            'whales': 0, 'sharks': 0, 'dolphins': 1, 'fish': 2,
            'octopus': 1, 'crabs': 0, 'shrimps': 1,
        },
        'holdersOut': {
            'whales': 0, 'sharks': 0, 'dolphins': 0, 'fish': 0,
            'octopus': 0, 'crabs': 0, 'shrimps': 0,
        },
    },
    {
        'timestamp': '2026-03-01T10:05:00.000Z',
        'totalHolders': 105,
        'netHolderChange': 5,
        'holderPercentChange': 5.0,
        'newHoldersByAcquisition': {'swap': 4, 'transfer': 0, 'airdrop': 1},
        'holdersIn': {
            'whales': 0, 'sharks': 1, 'dolphins': 0, 'fish': 2,
            'octopus': 1, 'crabs': 0, 'shrimps': 1,
        },
        'holdersOut': {
            'whales': 0, 'sharks': 0, 'dolphins': 0, 'fish': 0,
            'octopus': 0, 'crabs': 0, 'shrimps': 0,
        },
    },
]


# --- fetch_ohlcv tracking tests ----------------------------------------------

class FetchOHLCVTrackingTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='TRACK_FL001', anchor_event=T0,
        )
        PoolMapping.objects.create(
            coin_id='TRACK_FL001',
            pool_address='POOL_001',
            dex='pumpswap',
            source='dexscreener',
            created_at=T0,
        )

    @patch('pipeline.management.commands.fetch_ohlcv.fetch_ohlcv')
    def test_successful_run_creates_complete_entry(self, mock_fetch):
        mock_fetch.return_value = (FAKE_OHLCV_RAW, {'api_calls': 1})

        call_command('fetch_ohlcv', coin='TRACK_FL001')

        runs = U001PipelineRun.objects.filter(coin_id='TRACK_FL001', layer_id='FL-001')
        self.assertEqual(runs.count(), 1)

        run = runs.first()
        self.assertEqual(run.status, RunStatus.COMPLETE)
        self.assertEqual(run.records_loaded, 2)
        self.assertEqual(run.api_calls, 1)
        self.assertIsNotNone(run.completed_at)
        self.assertIsNotNone(run.time_range_start)
        self.assertIsNotNone(run.time_range_end)

    @patch('pipeline.management.commands.fetch_ohlcv.fetch_ohlcv')
    def test_failed_connector_records_error(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("Connection timeout")

        with self.assertRaises(CommandError):
            call_command('fetch_ohlcv', coin='TRACK_FL001')

        runs = U001PipelineRun.objects.filter(coin_id='TRACK_FL001', layer_id='FL-001')
        self.assertEqual(runs.count(), 1)

        run = runs.first()
        self.assertEqual(run.status, RunStatus.ERROR)
        self.assertIn("Connection timeout", run.error_message)
        self.assertIsNotNone(run.completed_at)

    @patch('pipeline.management.commands.fetch_ohlcv.fetch_ohlcv')
    def test_run_records_time_range(self, mock_fetch):
        mock_fetch.return_value = (FAKE_OHLCV_RAW, {'api_calls': 1})

        call_command('fetch_ohlcv', coin='TRACK_FL001')

        run = U001PipelineRun.objects.get(coin_id='TRACK_FL001', layer_id='FL-001')
        self.assertEqual(run.time_range_start, T0)
        self.assertIsNotNone(run.time_range_end)
        self.assertEqual(run.mode, RunMode.BOOTSTRAP)

    @patch('pipeline.management.commands.fetch_ohlcv.fetch_ohlcv')
    def test_refill_mode_tracked(self, mock_fetch):
        mock_fetch.return_value = (FAKE_OHLCV_RAW, {'api_calls': 1})

        start = '2026-03-01T10:00:00+00:00'
        end = '2026-03-01T10:10:00+00:00'
        call_command('fetch_ohlcv', coin='TRACK_FL001', start=start, end=end)

        run = U001PipelineRun.objects.get(coin_id='TRACK_FL001', layer_id='FL-001')
        self.assertEqual(run.mode, RunMode.REFILL)

    def test_no_run_created_for_missing_pool(self):
        """Validation failures before pipeline attempt don't create runs."""
        MigratedCoin.objects.create(
            mint_address='NO_POOL_COIN', anchor_event=T0,
        )

        with self.assertRaises(CommandError):
            call_command('fetch_ohlcv', coin='NO_POOL_COIN')

        self.assertEqual(
            U001PipelineRun.objects.filter(coin_id='NO_POOL_COIN').count(), 0
        )


# --- fetch_holders tracking tests --------------------------------------------

class FetchHoldersTrackingTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='TRACK_FL002', anchor_event=T0,
        )

    @patch('pipeline.management.commands.fetch_holders.fetch_holders')
    @patch('pipeline.management.commands.fetch_holders.get_daily_cu_used', return_value=0)
    def test_successful_run_creates_complete_entry(self, mock_cu, mock_fetch):
        mock_fetch.return_value = (FAKE_HOLDER_RAW, {'api_calls': 1, 'cu_consumed': 50})

        call_command('fetch_holders', coin='TRACK_FL002')

        runs = U001PipelineRun.objects.filter(coin_id='TRACK_FL002', layer_id='FL-002')
        self.assertEqual(runs.count(), 1)

        run = runs.first()
        self.assertEqual(run.status, RunStatus.COMPLETE)
        self.assertEqual(run.records_loaded, 2)
        self.assertEqual(run.api_calls, 1)
        self.assertEqual(run.cu_consumed, 50)
        self.assertIsNotNone(run.completed_at)

    @patch('pipeline.management.commands.fetch_holders.fetch_holders')
    @patch('pipeline.management.commands.fetch_holders.get_daily_cu_used', return_value=0)
    def test_failed_connector_records_error(self, mock_cu, mock_fetch):
        mock_fetch.side_effect = RuntimeError("API unreachable")

        with self.assertRaises(CommandError):
            call_command('fetch_holders', coin='TRACK_FL002')

        runs = U001PipelineRun.objects.filter(coin_id='TRACK_FL002', layer_id='FL-002')
        self.assertEqual(runs.count(), 1)

        run = runs.first()
        self.assertEqual(run.status, RunStatus.ERROR)
        self.assertIn("API unreachable", run.error_message)
        self.assertIsNotNone(run.completed_at)

    @patch('pipeline.management.commands.fetch_holders.fetch_holders')
    @patch('pipeline.management.commands.fetch_holders.get_daily_cu_used', return_value=0)
    def test_run_records_time_range_and_cu(self, mock_cu, mock_fetch):
        mock_fetch.return_value = (FAKE_HOLDER_RAW, {'api_calls': 2, 'cu_consumed': 100})

        call_command('fetch_holders', coin='TRACK_FL002')

        run = U001PipelineRun.objects.get(coin_id='TRACK_FL002', layer_id='FL-002')
        self.assertEqual(run.time_range_start, T0)
        self.assertIsNotNone(run.time_range_end)
        self.assertEqual(run.mode, RunMode.BOOTSTRAP)
        self.assertEqual(run.cu_consumed, 100)
        self.assertEqual(run.api_calls, 2)

    @patch('pipeline.management.commands.fetch_holders.get_daily_cu_used', return_value=0)
    def test_no_run_created_for_missing_coin(self, mock_cu):
        """Validation failures before pipeline attempt don't create runs."""
        with self.assertRaises(CommandError):
            call_command('fetch_holders', coin='NONEXISTENT')

        self.assertEqual(U001PipelineRun.objects.count(), 0)


# --- Zero-results completeness tests -----------------------------------------

class ZeroResultsCompletenessTest(TestCase):
    """Zero records from API: mature coin = WINDOW_COMPLETE, immature = PARTIAL."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='ZERO_RES_COIN', anchor_event=T0,
        )
        PoolMapping.objects.create(
            coin_id='ZERO_RES_COIN',
            pool_address='POOL_ZERO',
            dex='pumpswap',
            source='dexscreener',
            created_at=T0,
        )

    @patch('pipeline.management.commands.fetch_ohlcv.fetch_ohlcv')
    def test_ohlcv_zero_records_mature_sets_complete(self, mock_fetch):
        """Mature coin with zero API results = dead coin, window covered."""
        mock_fetch.return_value = ([], {'api_calls': 1})

        call_command('fetch_ohlcv', coin='ZERO_RES_COIN')

        status = U001PipelineStatus.objects.get(
            coin_id='ZERO_RES_COIN', layer_id='FL-001',
        )
        self.assertEqual(status.status, PipelineCompleteness.WINDOW_COMPLETE)

    @patch('pipeline.management.commands.fetch_holders.fetch_holders')
    @patch('pipeline.management.commands.fetch_holders.get_daily_cu_used', return_value=0)
    def test_holders_zero_records_mature_sets_complete(self, mock_cu, mock_fetch):
        """Mature coin with zero API results = window covered."""
        mock_fetch.return_value = ([], {'api_calls': 1, 'cu_consumed': 50})

        call_command('fetch_holders', coin='ZERO_RES_COIN')

        status = U001PipelineStatus.objects.get(
            coin_id='ZERO_RES_COIN', layer_id='FL-002',
        )
        self.assertEqual(status.status, PipelineCompleteness.WINDOW_COMPLETE)


class FetchTransactionsTrackingTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='TRACK_RD001',
            anchor_event=T0,
        )
        PoolMapping.objects.create(
            coin_id='TRACK_RD001',
            pool_address='POOL_RD001',
            dex='pumpswap',
            source='dexscreener',
            created_at=T0,
        )

    @patch('pipeline.connectors.helius.fetch_transactions')
    def test_fetch_failure_overwrites_partial_status_to_error(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError('upstream fetch failed')
        U001PipelineStatus.objects.create(
            coin_id='TRACK_RD001',
            layer_id='RD-001',
            status=PipelineCompleteness.PARTIAL,
        )

        with self.assertRaises(CommandError):
            call_command('fetch_transactions', coin='TRACK_RD001', source='helius')

        run = U001PipelineRun.objects.get(coin_id='TRACK_RD001', layer_id='RD-001')
        self.assertEqual(run.status, RunStatus.ERROR)
        self.assertIn('upstream fetch failed', run.error_message)

        status = U001PipelineStatus.objects.get(coin_id='TRACK_RD001', layer_id='RD-001')
        self.assertEqual(status.status, PipelineCompleteness.ERROR)
        self.assertIn('upstream fetch failed', status.last_error)

    @patch('pipeline.connectors.helius.fetch_transactions')
    def test_fetch_failure_without_existing_status_sets_error(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError('upstream fetch failed')

        with self.assertRaises(CommandError):
            call_command('fetch_transactions', coin='TRACK_RD001', source='helius')

        status = U001PipelineStatus.objects.get(coin_id='TRACK_RD001', layer_id='RD-001')
        self.assertEqual(status.status, PipelineCompleteness.ERROR)


# --- Connector metadata tests ------------------------------------------------

class MoralisMetadataTest(TestCase):
    @patch('pipeline.connectors.moralis.request_with_retry')
    @patch('pipeline.connectors.moralis.record_cu_used')
    @patch.dict('os.environ', {'MORALIS_API_KEY': 'test-key'})
    def test_returns_metadata_with_api_calls_and_cu(self, mock_record, mock_request):
        mock_request.return_value = {
            'result': [
                {
                    'timestamp': '2026-03-01T10:00:00.000Z',
                    'totalHolders': 100,
                },
            ],
            'cursor': None,
        }

        from pipeline.connectors.moralis import fetch_holders
        start = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 1, 10, 10, tzinfo=timezone.utc)

        records, meta = fetch_holders('MINT_TEST', start, end)

        self.assertIsInstance(records, list)
        self.assertIn('api_calls', meta)
        self.assertIn('cu_consumed', meta)
        self.assertGreaterEqual(meta['api_calls'], 1)
        self.assertGreaterEqual(meta['cu_consumed'], 50)
