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
    def test_helius_raises_guard_during_signature_discovery(self):
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
            with self.assertRaises(RuntimeError) as ctx:
                helius._fetch_signatures(
                    'POOL_HELIUS_LIMIT',
                    start=now - timedelta(hours=1),
                    end=now,
                )

        self.assertIn('exceeds free-tier guard', str(ctx.exception))

    def test_shyft_raises_guard_during_signature_discovery(self):
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
            with self.assertRaises(RuntimeError) as ctx:
                shyft._fetch_signatures(
                    'POOL_SHYFT_LIMIT',
                    start=now - timedelta(hours=1),
                    end=now,
                )

        self.assertIn('exceeds free-tier guard', str(ctx.exception))
