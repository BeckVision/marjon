from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from pipeline.connectors import helius, shyft


def _sig_page(count, block_time):
    return {
        'result': [
            {
                'signature': f'sig_{block_time}_{i}',
                'blockTime': block_time,
                'err': None,
            }
            for i in range(count)
        ]
    }


class RD001ConnectorFreeTierGuardTest(SimpleTestCase):
    def test_helius_continues_past_old_guard_threshold_during_signature_discovery(self):
        now = datetime.now(timezone.utc)
        block_time = int((now - timedelta(minutes=1)).timestamp())

        with patch.object(helius, '_next_api_key', return_value='test-key'), \
                patch.object(
                    helius,
                    'request_with_retry',
                    side_effect=[
                        _sig_page(1000, block_time),
                        _sig_page(1, block_time),
                    ],
                ):
            signatures, credits = helius._fetch_signatures(
                'POOL_HELIUS_LIMIT',
                start=now - timedelta(hours=1),
                end=now,
            )

        self.assertEqual(len(signatures), 1001)
        self.assertGreater(credits, 0)

    def test_shyft_continues_past_old_guard_threshold_during_signature_discovery(self):
        now = datetime.now(timezone.utc)
        block_time = int((now - timedelta(minutes=1)).timestamp())

        with patch.object(shyft, '_next_api_key', return_value='test-key'), \
                patch.object(
                    shyft,
                    'request_with_retry',
                    side_effect=[
                        _sig_page(1000, block_time),
                        _sig_page(1, block_time),
                    ],
                ):
            signatures = shyft._fetch_signatures(
                'POOL_SHYFT_LIMIT',
                start=now - timedelta(hours=1),
                end=now,
            )

        self.assertEqual(len(signatures), 1001)

    def test_shyft_parse_fallback_splits_failed_batch(self):
        chunk = [f'sig_{i}' for i in range(20)]

        def fake_parse(batch):
            if len(batch) == 20:
                raise RuntimeError('Server disconnected')
            return [{'signatures': batch}]

        with patch.object(shyft, '_parse_one_batch', side_effect=fake_parse):
            parsed = shyft._parse_with_fallback(chunk)

        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]['signatures'], chunk[:10])
        self.assertEqual(parsed[1]['signatures'], chunk[10:])

    def test_shyft_parse_fallback_stops_at_minimum_batch_size(self):
        chunk = [f'sig_{i}' for i in range(shyft.MIN_PARSE_BATCH_SIZE)]

        with patch.object(
            shyft, '_parse_one_batch', side_effect=RuntimeError('Server disconnected')
        ):
            with self.assertRaises(RuntimeError) as ctx:
                shyft._parse_with_fallback(chunk)

        self.assertIn('Server disconnected', str(ctx.exception))

    def test_shyft_parse_selected_respects_configured_batch_size(self):
        signatures = [f'sig_{i}' for i in range(25)]

        with patch.object(shyft, 'PARSE_BATCH_SIZE', 10), \
                patch.object(
                    shyft,
                    '_parse_with_fallback',
                    side_effect=lambda batch: [{'batch_size': len(batch)}],
                ) as parse_batch:
            parsed = shyft._parse_selected(signatures, max_workers=1)

        self.assertEqual(
            [row['batch_size'] for row in parsed],
            [10, 10, 5],
        )
        self.assertEqual(
            [len(call.args[0]) for call in parse_batch.call_args_list],
            [10, 10, 5],
        )

    def test_helius_parse_transactions_respects_configured_batch_size(self):
        signatures = [f'sig_{i}' for i in range(25)]

        with patch.object(helius, 'PARSE_BATCH_SIZE', 10), \
                patch.object(
                    helius,
                    '_parse_one_batch',
                    side_effect=lambda batch: ([{'batch_size': len(batch)}], 100),
                ) as parse_batch:
            parsed, credits = helius._parse_transactions(signatures, max_workers=1)

        self.assertEqual(
            [row['batch_size'] for row in parsed],
            [10, 10, 5],
        )
        self.assertEqual(
            [len(call.args[0]) for call in parse_batch.call_args_list],
            [10, 10, 5],
        )
        self.assertEqual(credits, 300)
