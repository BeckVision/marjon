from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from pipeline.audits.rd001_solscan import compare_row_to_solscan, compare_window_to_solscan
from warehouse.models import MigratedCoin, RawTransaction


class RD001SolscanAuditHelpersTest(TestCase):
    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='SOLSCAN_COIN',
            anchor_event=timezone.now() - timedelta(hours=2),
        )
        self.row = RawTransaction.objects.create(
            coin=self.coin,
            timestamp=timezone.now().replace(microsecond=0),
            tx_signature='SOLSCAN_SIG_1',
            trade_type='BUY',
            wallet_address='SOLSCAN_WALLET',
            token_amount=10,
            sol_amount=1,
            pool_address='SOLSCAN_POOL',
            tx_fee='0.000005',
            lp_fee=0,
            protocol_fee=0,
            coin_creator_fee=0,
        )

    def test_compare_row_to_solscan_matches_basic_fields(self):
        payload = {
            'success': True,
            'data': {
                'tx_hash': self.row.tx_signature,
                'status': 'Success',
                'block_time': int(self.row.timestamp.timestamp()),
                'fee': 5000,
                'signer': self.row.wallet_address,
            },
        }

        result = compare_row_to_solscan(self.row, payload)

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['warnings'], [])

    def test_compare_window_to_solscan_detects_missing_signature(self):
        start = self.row.timestamp - timedelta(minutes=30)
        end = self.row.timestamp + timedelta(minutes=5)
        payload = {
            'success': True,
            'data': [
                {
                    'tx_hash': 'OTHER_SIG',
                    'block_time': int(self.row.timestamp.timestamp()),
                },
            ],
        }

        result = compare_window_to_solscan(
            coin_id=self.row.coin_id,
            pool_address=self.row.pool_address,
            start=start,
            end=end,
            warehouse_signatures=[self.row.tx_signature],
            payload=payload,
        )

        self.assertEqual(result['status'], 'finding')
        self.assertIn('missing_signatures', result['findings'])
        self.assertIn('extra_signatures', result['findings'])
