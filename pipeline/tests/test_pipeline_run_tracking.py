"""Tests for U001PipelineRun tracking in fetch_ohlcv and fetch_holders."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from warehouse.models import (
    MigratedCoin, OHLCVCandle, PoolMapping,
    RunMode, RunStatus, U001PipelineRun,
)

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


# --- Fixtures ----------------------------------------------------------------

FAKE_OHLCV_RAW = [
    {
        'time_open': '2026-03-01T10:00:00Z',
        'time_close': '2026-03-01T10:05:00Z',
        'open': 10.0, 'high': 12.0, 'low': 9.0, 'close': 11.0,
        'volume': 100.0,
    },
    {
        'time_open': '2026-03-01T10:05:00Z',
        'time_close': '2026-03-01T10:10:00Z',
        'open': 11.0, 'high': 13.0, 'low': 10.0, 'close': 12.0,
        'volume': 200.0,
    },
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
            source='dexpaprika',
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


# --- Connector metadata tests ------------------------------------------------

class DexPaprikaMetadataTest(TestCase):
    @patch('pipeline.connectors.dexpaprika._request_with_retry')
    def test_returns_metadata_with_api_calls(self, mock_request):
        mock_request.return_value = [
            {
                'time_open': '2026-03-01T10:00:00Z',
                'time_close': '2026-03-01T10:05:00Z',
                'open': 10.0, 'high': 12.0, 'low': 9.0, 'close': 11.0,
                'volume': 100.0,
            },
        ]

        from pipeline.connectors.dexpaprika import fetch_ohlcv
        start = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 1, 10, 10, tzinfo=timezone.utc)

        records, meta = fetch_ohlcv('POOL_TEST', start, end)

        self.assertIsInstance(records, list)
        self.assertIn('api_calls', meta)
        self.assertGreaterEqual(meta['api_calls'], 1)


class MoralisMetadataTest(TestCase):
    @patch('pipeline.connectors.moralis._request_with_retry')
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
